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


@pytest.mark.parametrize("aweme_list", [None, 42, "invalid", {"aweme_id": "123"}])
def test_douyin_malformed_feed_uses_the_next_source(aweme_list):
    from app.resolver.douyin import DouyinResolver

    requests = []

    def response(request):
        requests.append(request.headers["host"])
        if request.headers["host"] == "api5-normal-c-hl.amemv.com":
            return httpx.Response(200, json={"aweme_list": aweme_list})
        return httpx.Response(
            200,
            json={
                "aweme_list": [
                    {
                        "aweme_id": "123",
                        "video": {
                            "play_addr": {"url_list": ["https://video.example/fallback.mp4"]}
                        },
                    }
                ]
            },
        )

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    video = asyncio.run(DouyinResolver(network).resolve("https://www.douyin.com/video/123"))
    assert video.video_url == "https://video.example/fallback.mp4"
    assert requests == ["api5-normal-c-hl.amemv.com", "aweme.snssdk.com"]


@pytest.mark.parametrize("url_list", [None, 42, "https://wrong.example/v", {"0": "bad"}])
def test_douyin_non_list_play_addresses_allow_the_download_address(url_list):
    from app.resolver.douyin import DouyinResolver

    video = DouyinResolver.from_item(
        {
            "video": {
                "play_addr": {"url_list": url_list},
                "download_addr": {"url_list": ["https://video.example/download.mp4"]},
            },
        }
    )
    assert video.video_url == "https://video.example/download.mp4"


@pytest.mark.parametrize("item_list", [None, 42, "invalid", {"aweme_id": "123"}])
def test_douyin_malformed_page_items_do_not_hide_another_exact_match(item_list):
    from app.resolver.douyin import DouyinResolver

    def response(request):
        if request.headers["host"] != "www.iesdouyin.com":
            return httpx.Response(200, json={"aweme_list": []})
        value = {
            "loaderData": {
                "invalid": {"videoInfoRes": {"item_list": item_list}},
                "valid": {
                    "videoInfoRes": {
                        "item_list": [
                            {
                                "aweme_id": "123",
                                "video": {
                                    "play_addr": {"url_list": ["https://video.example/page.mp4"]}
                                },
                            }
                        ]
                    }
                },
            }
        }
        return httpx.Response(200, text="window._ROUTER_DATA = " + json.dumps(value))

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    video = asyncio.run(DouyinResolver(network).resolve("https://www.douyin.com/video/123"))
    assert video.video_url == "https://video.example/page.mp4"


@pytest.mark.parametrize("resolver_kind", ["douyin", "huoshan"])
def test_configured_provider_normalizes_metadata_and_preserves_url_contract(resolver_kind):
    from app.resolver.douyin import DouyinResolver
    from app.resolver.huoshan import HuoshanResolver

    resolver_class, share = (
        (DouyinResolver, "https://www.douyin.com/video/123")
        if resolver_kind == "douyin"
        else (HuoshanResolver, "https://reflow.huoshan.com/share/item/123/")
    )

    def response(request):
        assert request.headers["host"] == "provider.example"
        assert request.headers["authorization"] == "Bearer test-provider-token"
        assert request.url.params["url"] == share
        assert request.url.params["region"] == "cn"
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "video_url": "https://video.example/provider.mp4",
                    "title": "标题\ud800尾😀",
                    "author": {"name": "作者\udfff"},
                    "music_url": "https://music.example/bgm",
                }
            ).encode("utf-8"),
        )

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    video = asyncio.run(
        resolver_class(
            network, "https://provider.example/parse?region=cn", "test-provider-token"
        ).resolve(share)
    )
    assert video.video_url == "https://video.example/provider.mp4"
    assert video.title == "标题\ufffd尾😀"
    assert video.author == "作者\ufffd"
    assert video.original_audio_url is None
    json.dumps({"title": video.title, "author": video.author}, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize(
    "status,body",
    [
        (503, b'{"detail":"private upstream error"}'),
        (200, b"not-json"),
        (200, b"null"),
        (200, b"[]"),
        (200, b"\xff"),
        (200, b'{"video_url":null}'),
        (200, b'{"music_url":"https://music.example/bgm"}'),
    ],
)
def test_configured_provider_failure_is_public_and_does_not_change_source(status, body):
    from app.resolver.douyin import DouyinResolver

    requested = []

    def response(request):
        requested.append(request.headers["host"])
        return httpx.Response(status, content=body)

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    with pytest.raises(MediaError) as error:
        asyncio.run(
            DouyinResolver(network, "https://provider.example/parse").resolve(
                "https://www.douyin.com/video/123"
            )
        )
    assert error.value.code == "resolve_failed"
    assert "private" not in error.value.message
    assert requested == ["provider.example"]


@pytest.mark.parametrize("first_failure", ["non_200", "invalid_json", "network_timeout"])
def test_douyin_feed_failures_reach_the_exact_matching_page(first_failure):
    from app.resolver.douyin import DouyinResolver

    requested = []

    def response(request):
        host = request.headers["host"]
        requested.append(host)
        if host == "api5-normal-c-hl.amemv.com":
            if first_failure == "network_timeout":
                raise httpx.ReadTimeout("upstream timeout", request=request)
            return httpx.Response(503 if first_failure == "non_200" else 200, text="invalid")
        if host == "aweme.snssdk.com":
            return httpx.Response(
                200,
                json={
                    "aweme_list": [
                        {
                            "aweme_id": "999",
                            "video": {"play_addr": {"url_list": ["https://wrong.example/video"]}},
                        }
                    ]
                },
            )
        value = {
            "loaderData": {
                "page": {
                    "videoInfoRes": {
                        "item_list": [
                            {"aweme_id": "123", "video": {"play_addr": {"url_list": 5}}},
                            {
                                "aweme_id": "123",
                                "video": {
                                    "play_addr": {
                                        "url_list": [None, "https://video.example/page.mp4"]
                                    },
                                },
                            },
                        ]
                    }
                }
            }
        }
        return httpx.Response(200, text="window._ROUTER_DATA = " + json.dumps(value))

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    video = asyncio.run(DouyinResolver(network).resolve("https://www.douyin.com/video/123"))
    assert video.video_url == "https://video.example/page.mp4"
    assert requested == [
        "api5-normal-c-hl.amemv.com",
        "aweme.snssdk.com",
        "www.iesdouyin.com",
    ]


def test_huoshan_provider_resolves_short_share_without_fetching_it_first():
    from app.resolver.huoshan import HuoshanResolver

    share = "https://share.huoshan.com/hotsoon/s/sample/"
    requests = []

    def response(request):
        requests.append(request.headers["host"])
        assert request.url.params["url"] == share
        assert request.headers.get("authorization") is None
        return httpx.Response(
            200,
            json={
                "video_url": "https://video.example/huoshan-provider.mp4",
                "title": {"unexpected": "metadata"},
                "author": ["unexpected metadata"],
            },
        )

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    video = asyncio.run(HuoshanResolver(network, "https://provider.example/parse").resolve(share))
    assert requests == ["provider.example"]
    assert video.video_url == "https://video.example/huoshan-provider.mp4"
    assert video.title == video.author == ""


def test_huoshan_rejects_another_platform_before_calling_the_provider():
    from app.resolver.huoshan import HuoshanResolver

    def response(request):
        pytest.fail("wrong-platform input must not cause a network request")

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    with pytest.raises(MediaError) as error:
        asyncio.run(
            HuoshanResolver(network, "https://provider.example/parse").resolve(
                "https://www.douyin.com/video/123"
            )
        )
    assert error.value.code == "invalid_input"
    assert error.value.status == 400


def test_huoshan_short_share_uses_the_redirected_exact_query_id():
    from app.resolver.huoshan import HuoshanResolver

    requested = []

    def response(request):
        requested.append((request.headers["host"], request.url.path))
        if request.url.path == "/hotsoon/s/sample/":
            return httpx.Response(
                302, headers={"location": "https://reflow.huoshan.com/?item_id=123"}
            )
        if request.url.path == "/":
            return httpx.Response(200, text="video landing page")
        assert request.url.params["item_id"] == "123"
        return httpx.Response(
            200,
            json={
                "data": {
                    "item_info": {
                        "id": "123",
                        "url": "https://video.example/short-share.mp4",
                        "title": "分享视频",
                        "author": "作者",
                    }
                }
            },
        )

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    video = asyncio.run(
        HuoshanResolver(network).resolve("https://share.huoshan.com/hotsoon/s/sample/")
    )
    assert video.video_url == "https://video.example/short-share.mp4"
    assert video.author == "作者"
    assert requested == [
        ("share.huoshan.com", "/hotsoon/s/sample/"),
        ("reflow.huoshan.com", "/"),
        ("share.huoshan.com", "/api/item/info"),
    ]


@pytest.mark.parametrize(
    "destination,message",
    [
        ("https://www.douyin.com/video/123", "失效"),
        ("https://reflow.huoshan.com/home/", "无法识别"),
        ("https://reflow.huoshan.com/?item_id=invalid", "无法识别"),
    ],
)
def test_huoshan_redirect_without_matching_platform_and_id_never_calls_media_api(
    destination, message
):
    from app.resolver.huoshan import HuoshanResolver

    def response(request):
        assert request.url.path != "/api/item/info"
        if request.url.path == "/hotsoon/s/sample/":
            return httpx.Response(302, headers={"location": destination})
        return httpx.Response(200, text="non-video landing page")

    network = SafeHTTP(transport=httpx.MockTransport(response), resolve=public_dns)
    with pytest.raises(MediaError, match=message) as error:
        asyncio.run(HuoshanResolver(network).resolve("https://share.huoshan.com/hotsoon/s/sample/"))
    assert error.value.code == "resolve_failed"


@pytest.mark.parametrize(
    "status,payload",
    [
        (503, {"detail": "private upstream response"}),
        (200, {"data": None}),
        (200, {"data": "invalid"}),
        (200, {"data": {"item_info": ["invalid"]}}),
        (200, {"data": {"item_info": 123}}),
        (200, {"data": {"item_info": None}}),
    ],
)
def test_huoshan_api_failures_and_malformed_items_return_public_errors(status, payload):
    from app.resolver.huoshan import HuoshanResolver

    network = SafeHTTP(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)),
        resolve=public_dns,
    )
    with pytest.raises(MediaError) as error:
        asyncio.run(HuoshanResolver(network).resolve("https://reflow.huoshan.com/share/item/123/"))
    assert error.value.code == "resolve_failed"
    assert "private" not in error.value.message


@pytest.mark.parametrize(
    "url,code",
    [
        (None, "resolve_failed"),
        (123, "resolve_failed"),
        ("http://127.0.0.1/private-video", "unsafe_url"),
    ],
)
def test_huoshan_matching_id_requires_a_valid_video_url_and_never_uses_music(url, code):
    from app.resolver.huoshan import HuoshanResolver

    network = SafeHTTP(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "data": {
                        "item_info": {
                            "item_id": "123",
                            "url": url,
                            "music_url": "https://music.example/only-bgm",
                        }
                    }
                },
            )
        ),
        resolve=public_dns,
    )
    with pytest.raises(MediaError) as error:
        asyncio.run(HuoshanResolver(network).resolve("https://reflow.huoshan.com/share/item/123/"))
    assert error.value.code == code
