""" Supervisor API server """

import logging
import os

from flask import Flask, make_response, request
from flask_cors import CORS

from app.services import SupervisorService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = Flask(__name__)
CORS(app)

ONOS_BASE_URL = "http://127.0.0.1:8181/onos/v1"
DEPLOYER_BASE_URL = "http://127.0.0.1:5000/deploy"

supervisor = SupervisorService(ONOS_BASE_URL, DEPLOYER_BASE_URL)


@app.route("/", methods=["GET"])
def home():
    """ Check if supervisor is running """
    return "Lumi Supervisor APIs"


@app.route("/supervise", methods=["POST"])
def supervise():
    """
    Receives the path calculated by the deployer.

    Expected JSON body:
    {
        "path":            [[i, j], ...],
        "access_delay_ms": 0.0           # optional
    }
    """
    data = request.get_json(silent=True, force=True)
    if not data:
        return make_response({"error": "invalid or missing JSON body"}, 400)

    if "path" not in data:
        return make_response({"error": "missing field: path"}, 400)

    supervisor.update(
        path=data["path"],
        access_delay_ms=float(data.get("access_delay_ms", 0.0)),
    )

    return make_response({"status": "ok", "message": "Path received, monitoring started"}, 200)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5151))
    logging.info("Starting supervisor on port %d", port)
    app.run(debug=True, port=port, host="0.0.0.0")
