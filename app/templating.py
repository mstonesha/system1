from fastapi.templating import Jinja2Templates

from app.config import get_settings


class LiveSettings:
    def __getattr__(self, name: str):
        return getattr(get_settings(), name)


templates = Jinja2Templates(
    directory="app/templates",
)

templates.env.globals["settings"] = LiveSettings()
