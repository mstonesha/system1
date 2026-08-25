from fastapi.templating import Jinja2Templates

from app.config import get_settings


class LiveSettings:
    def __getattr__(self, name: str):
        return getattr(get_settings(), name)


class AppTemplates(Jinja2Templates):
    def TemplateResponse(self, request, name, context=None, **kwargs):
        if not hasattr(request.state, "csrf_token"):
            request.state.csrf_token = ""
        if not hasattr(request.state, "browser_session"):
            request.state.browser_session = None
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Cache-Control", "no-store")
        kwargs["headers"] = headers
        return super().TemplateResponse(
            request=request,
            name=name,
            context=context,
            **kwargs,
        )


templates = AppTemplates(
    directory="app/templates",
)

templates.env.globals["settings"] = LiveSettings()
