"""
Flask API Blueprint.
Conforms to PRD.md Section 9 and SYSTEM_DESIGN.md Section 5 & Section 8.
"""

import time
import json
import logging
import threading
import tempfile
from typing import Any
from flask import Blueprint, request, jsonify, send_file

import config
import db
import sync
import backup
import time_utils

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)

# Module-level sync lock and progress state shared across the process (SYSTEM_DESIGN.md Section 8)
_sync_lock = threading.Lock()
_progress: dict[str, Any] = {
    "running": False,
    "index": 0,
    "window_index": 0,
    "total": 0,
    "window_total": 0,
    "started_at": None,
    "last_result": None,
}


def _run_sync():
    """Background worker function for POST /api/sync."""
    try:
        result = sync.run(progress=_progress)
        _progress["last_result"] = result
    except Exception as e:
        logger.exception("Sync background thread failed with exception")
        _progress["last_result"] = {
            "fatal": str(e),
            "errors": [{"reason": str(e)}],
            "warnings": [],
            "finished_at": int(time.time() * 1000),
        }
    finally:
        _progress["running"] = False
        _progress["index"] = 0
        _progress["window_index"] = 0
        _progress["total"] = 0
        _progress["window_total"] = 0
        _sync_lock.release()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@api_bp.route("/meta", methods=["GET"])
@api_bp.route("/api/meta", methods=["GET"])
def get_meta():
    """
    GET /api/meta (PRD Section 9.1 & SYSTEM_DESIGN.md Section 5.1).
    Returns last_synced_at, order_count, position_count, schema_version.
    """
    try:
        data = db.fetch_meta()
        return jsonify(data), 200
    except Exception as e:
        logger.exception("Error in GET /api/meta")
        return jsonify({"error": "internal error"}), 500


@api_bp.route("/positions", methods=["GET"])
@api_bp.route("/api/positions", methods=["GET"])
def get_positions():
    """
    GET /api/positions?from=YYYY-MM-DD&to=YYYY-MM-DD (PRD Section 9.2 & SYSTEM_DESIGN.md Section 5.2).
    Validates and clamps date range to [CUTOFF_DATE, today IST].
    Returns array of position objects.
    """
    from_param = request.args.get("from")
    to_param = request.args.get("to")

    try:
        clamped_from, clamped_to, from_ts, to_ts = time_utils.validate_and_clamp_range(from_param, to_param)
    except ValueError as e:
        err_msg = str(e)
        logger.warning(f"Invalid positions request params from={from_param!r}, to={to_param!r}: {err_msg}")
        return jsonify({"error": err_msg}), 400
    except Exception as e:
        logger.exception("Unexpected error validating positions date range")
        return jsonify({"error": "internal error"}), 500

    try:
        positions = db.fetch_positions(from_ts=from_ts, to_ts=to_ts)
        return jsonify(positions), 200
    except Exception as e:
        logger.exception("Error querying positions from database")
        return jsonify({"error": "internal error"}), 500


@api_bp.route("/balance", methods=["GET"])
@api_bp.route("/api/balance", methods=["GET"])
def get_balance():
    """
    GET /api/balance (PRD Section 9.3 & SYSTEM_DESIGN.md Section 5.3).
    Returns latest USDT balance snapshot, or {"balance": null} if never synced.
    """
    try:
        snap = db.fetch_balance()
        if snap.get("balance") is None:
            return jsonify({"balance": None}), 200
        return jsonify(snap), 200
    except Exception as e:
        logger.exception("Error in GET /api/balance")
        return jsonify({"error": "internal error"}), 500


@api_bp.route("/sync", methods=["POST"])
@api_bp.route("/api/sync", methods=["POST"])
def post_sync():
    """
    POST /api/sync (PRD Section 9.4 & SYSTEM_DESIGN.md Section 5.4).
    Acquires the sync lock without blocking.
    Returns 202 {"started": True} or 409 {"error": "sync already in progress"}.
    """
    if not _sync_lock.acquire(blocking=False):
        return jsonify({"error": "sync already in progress"}), 409

    now_epoch_ms = int(time.time() * 1000)
    _progress.update(
        running=True,
        index=0,
        window_index=0,
        total=0,
        window_total=0,
        started_at=now_epoch_ms,
        last_result=None,
    )

    t = threading.Thread(target=_run_sync, daemon=True)
    t.start()
    return jsonify({"started": True}), 202


@api_bp.route("/sync/status", methods=["GET"])
@api_bp.route("/api/sync/status", methods=["GET"])
def get_sync_status():
    """
    GET /api/sync/status (PRD Section 9.5 & Section 7.4 & SYSTEM_DESIGN.md Section 5.5).
    Returns running state, window progress, started_at, and last_result.
    """
    last_res = _progress.get("last_result")
    if last_res is None:
        raw_last = db.get_meta("last_sync_result")
        if raw_last:
            try:
                last_res = json.loads(raw_last)
            except Exception:
                last_res = None

    running = _progress.get("running", False)
    return jsonify({
        "running": running,
        "window_index": _progress.get("window_index", 0) if running else 0,
        "window_total": _progress.get("window_total", 0) if running else 0,
        "started_at": _progress.get("started_at"),
        "last_result": last_res,
    }), 200


@api_bp.route("/backup/download", methods=["GET"])
@api_bp.route("/api/backup/download", methods=["GET"])
def download_backup():
    """
    GET /api/backup/download
    Safely creates a consistent SQLite backup snapshot and returns it as a download.
    """
    try:
        conn = db.get_db()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_path = tmp.name
        tmp.close()
        backup.create_sqlite_snapshot(conn, tmp_path)
        date_str = time.strftime("%Y%m%d_%H%M%S")
        return send_file(
            tmp_path,
            as_attachment=True,
            download_name=f"trades_backup_{date_str}.db",
            mimetype="application/x-sqlite3",
        )
    except Exception as e:
        logger.exception("Failed to generate database download")
        return jsonify({"error": "Failed to create database backup"}), 500

