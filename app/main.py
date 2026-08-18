from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="System 1")


@app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>System 1</title>
        </head>
        <body>
            <h1>System 1</h1>
            <p>The application is running.</p>
        </body>
    </html>
    """