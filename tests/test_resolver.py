import asyncio
import json

import httpx
import pytest

from app.services.errors import MediaError
from app.services.network import SafeHTTP


def test_share_text_accepts_one_platform_link_and_rejects_multiple_or_foreign_urls():
    from app.resolver.registry import extract_share_url

    assert extract_share_url("看看这个视频 https://v.douyin.com/abc/ 复制打开！") == (
        "douyin",
        "https://v.douyin.com/abc/",
    )
    assert extract_share_url("https://share.huoshan.com/hotsoon/s/xyz/")[0] == "huoshan"
    for text in (
        "https://evil.example/v",
        "https://douyin.com.evil.example/v",
        "https://v.douyin.com/a/ https://v.douyin.com/b/",
        "没有链接",
    ):
        with pytest.raises(MediaError):
            extract_share_url(text)


async def public_dns(host, port):
    return ["93.184.216.34"]


def test_douyin_requires_exact_video_id_and_ignores_music():
    from app.resolver.douyin import DouyinResolver

    async def response(request):
        return httpx.Response(
            200,
            json={
                "aweme_list": [
                    {
                        "aweme_id": "999",
                        "video": {"play_addr": {"url_list": ["https://wrong.example/v"]}},
                    },
                    {
                        "aweme_id": "123",
                        "desc": "测试视频",
                        "author": {"nickname": "作者"},
                        "video": {"play_addr": {"url_list": ["https://video.example/v.mp4"]}},
                        "music": {"play_url": {"url_list": ["https://music.example/bgm"]}},
                    },
                ]
            },
        )

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    video = asyncio.run(DouyinResolver(network).resolve("https://www.douyin.com/video/123"))
    assert video.video_url == "https://video.example/v.mp4"
    assert video.original_audio_url is None
    assert video.title == "测试视频"


def test_recommendation_without_target_cannot_become_a_result():
    from app.resolver.douyin import DouyinResolver

    def response(request):
        return httpx.Response(
            200,
            json={
                "aweme_list": [
                    {
                        "aweme_id": "999",
                        "video": {"play_addr": {"url_list": ["https://wrong.example/v"]}},
                    }
                ]
            },
        )

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    with pytest.raises(MediaError, match="解析"):
        asyncio.run(DouyinResolver(network).resolve("https://www.douyin.com/video/123"))


def test_huoshan_reads_video_only_and_provider_credentials_do_not_follow_redirects():
    from app.resolver.huoshan import HuoshanResolver

    def response(request):
        assert request.headers.get("authorization") is None
        return httpx.Response(
            200,
            json={
                "data": {
                    "item_info": {
                        "url": "https://video.example/huoshan.mp4",
                        "title": "火山样本",
                        "item_id": "123",
                    }
                }
            },
        )

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    video = asyncio.run(
        HuoshanResolver(network).resolve("https://reflow.huoshan.com/share/item/123/")
    )
    assert video.video_url.endswith("huoshan.mp4")

    def redirect(request):
        assert request.url.params["url"].startswith("https://reflow.huoshan.com/")
        assert request.headers["authorization"] == "Bearer secret-key"
        return httpx.Response(302, headers={"location": "https://other.example/"})

    network = SafeHTTP(transport=httpx.MockTransport(redirect), resolve=public_dns)
    with pytest.raises(MediaError):
        asyncio.run(
            HuoshanResolver(network, "https://provider.example/parse", "secret-key").resolve(
                "https://reflow.huoshan.com/share/item/123/"
            )
        )


def test_router_json_preserves_nested_structure():
    from app.resolver.provider import router_data

    value = {"loaderData": {"video_(id)/page": {"nested": {"text": "a}b"}}}}
    assert router_data("<script>window._ROUTER_DATA = " + json.dumps(value) + ";</script>") == value


@pytest.mark.parametrize("returned_id", ["999", None])
def test_huoshan_wrong_or_missing_id_cannot_become_a_result(returned_id):
    from app.resolver.huoshan import HuoshanResolver

    def response(request):
        return httpx.Response(
            200,
            json={
                "data": {
                    "item_info": {
                        "item_id": returned_id,
                        "url": "https://wrong.example/video.mp4",
                    }
                }
            },
        )

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    with pytest.raises(MediaError, match="匹配"):
        asyncio.run(HuoshanResolver(network).resolve("https://reflow.huoshan.com/share/item/123/"))
