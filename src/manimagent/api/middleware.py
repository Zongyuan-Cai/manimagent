from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class UploadLimitMiddleware:
    """Bound multipart bytes while receiving, before Starlette spools the entire upload."""

    def __init__(self, app, max_bytes: int):
        self.app, self.limit = app, max_bytes + 65536

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/v2/files" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if length > self.limit:
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "upload_too_large",
                        "message": "文件请求超过上传上限",
                        "retryable": False,
                        "details": {},
                    }
                },
            )
            return await response(scope, receive, send)
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.limit:
                raise HTTPException(413, "文件请求超过上传上限")
            return message

        return await self.app(scope, limited_receive, send)
