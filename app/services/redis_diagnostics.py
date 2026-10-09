"""Fixed Redis failure categories; exception text may contain credentials."""

FAILURE_MESSAGES = {
    "client_unavailable": "Redis 客户端无法导入，请运行 ./scripts/setup.sh 同步项目依赖",
    "timeout": "连接或读写超时，请检查 Redis 网络可达性及超时配置",
    "authentication": "认证失败，请核对 Redis 用户名和密码",
    "permission": "权限不足，请核对 Redis ACL 的命令与 key 授权",
    "connection": "连接失败，请检查 Redis 服务、网络和 TLS 配置",
    "redis_error": "Redis 命令执行失败，请检查服务状态和命令支持",
    "invalid_state": "保护数据或客户端配置无效，请检查部署配置与账本状态",
    "unexpected_error": "发生未分类错误，请检查运行环境",
}


def redis_failure_reason(error: Exception) -> str:
    """Map SDK exceptions and Redis' Lua ACL wrapper to fixed, safe reasons."""
    if isinstance(error, ImportError):
        return "client_unavailable"
    try:
        from redis import exceptions
    except ImportError:
        pass
    else:
        # Redis wraps a script's denied command in ERR, not NOPERM. Inspect only
        # this fixed server prefix; never copy any response text into diagnostics.
        if (
            isinstance(error, exceptions.ResponseError)
            and error.args
            and isinstance(error.args[0], str)
            and error.args[0].startswith("ACL failure in script: ")
        ):
            return "permission"
        # Authentication/authorization errors also inherit ConnectionError.
        for kinds, reason in (
            ((exceptions.AuthenticationError,), "authentication"),
            ((exceptions.AuthorizationError, exceptions.NoPermissionError), "permission"),
            ((exceptions.TimeoutError,), "timeout"),
            ((exceptions.ConnectionError,), "connection"),
            ((exceptions.RedisError,), "redis_error"),
        ):
            if isinstance(error, kinds):
                return reason
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, OSError):
        return "connection"
    if isinstance(error, ValueError):
        return "invalid_state"
    return "unexpected_error"
