"""
Flask Application Factory.
Conforms to SYSTEM_DESIGN.md Section 3.1, Section 8, and Section 12.
"""

from pathlib import Path
from flask import Flask, send_from_directory
import config
import db
from api import api_bp

BASE_DIR = Path(__file__).resolve().parent


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static"
    )

    # Initialize DB schema idempotently on startup
    db.init_db()

    # Register API blueprint
    app.register_blueprint(api_bp)

    # Register database connection teardown
    app.teardown_appcontext(db.close_db)

    # Serve index.html at root
    @app.route("/")
    def index():
        return send_from_directory(str(BASE_DIR / "static"), "index.html")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

