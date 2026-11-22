import os
from backend.app import app

if __name__ == "__main__":
    # Debug is OFF unless FLASK_DEBUG=1 (set it in .env for local development).
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
