import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from postgrest import APIError
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)

# Comma-separated exact frontend origins, e.g. https://my-frontend.onrender.com.
# Local defaults keep common development servers working before deployment.
cors_origins = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:4173",
    ).split(",")
    if origin.strip()
]
CORS(
    app,
    resources={r"/api/*": {"origins": cors_origins}},
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
if not supabase_url or not supabase_key:
    raise RuntimeError(
        "SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be configured"
    )

supabase: Client = create_client(
    supabase_url,
    supabase_key,
)


def database_error(error: APIError):
    """Return a consistent JSON response for Supabase/PostgREST errors."""
    return jsonify({"error": error.message, "code": error.code}), 500


@app.get("/")
def index():
    return jsonify({"message": "Events API", "endpoints": [
        "GET /api/events",
        "GET /api/events/<id>",
        "POST /api/registrations",
        "GET /api/me/registrations?user_id=<uuid>",
        "GET /api/events/<id>/registrations",
    ]})


@app.get("/api/events")
def list_events():
    try:
        response = supabase.table("events").select("*").execute()
    except APIError as error:
        return database_error(error)
    return jsonify(response.data)


@app.get("/api/events/<event_id>")
def get_event(event_id):
    try:
        response = (
            supabase.table("events")
            .select("*")
            .eq("id", event_id)
            .maybe_single()
            .execute()
        )
    except APIError as error:
        return database_error(error)
    if response.data is None:
        return jsonify({"error": "Event not found"}), 404
    return jsonify(response.data)


@app.post("/api/registrations")
def create_registration():
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    event_id = body.get("event_id")
    if not user_id or not event_id:
        return jsonify({"error": "user_id and event_id are required"}), 400

    try:
        event_result = (
            supabase.table("events")
            .select("id,capacity")
            .eq("id", event_id)
            .maybe_single()
            .execute()
        )
        if event_result.data is None:
            return jsonify({"error": "Event not found"}), 404

        existing = (
            supabase.table("registrations")
            .select("id")
            .eq("user_id", user_id)
            .eq("event_id", event_id)
            .execute()
        )
        if existing.data:
            return jsonify({"error": "User is already registered for this event"}), 409

        count_result = (
            supabase.table("registrations")
            .select("id", count="exact")
            .eq("event_id", event_id)
            .execute()
        )
        capacity = event_result.data.get("capacity")
        if capacity is not None and count_result.count >= capacity:
            return jsonify({"error": "Event is at capacity"}), 409

        inserted = (
            supabase.table("registrations")
            .insert({"user_id": user_id, "event_id": event_id})
            .execute()
        )
    except APIError as error:
        # A unique constraint on (user_id, event_id) also protects against
        # simultaneous duplicate requests.
        if error.code == "23505":
            return jsonify({"error": "User is already registered for this event"}), 409
        return database_error(error)

    return jsonify(inserted.data[0]), 201


@app.get("/api/me/registrations")
def my_registrations():
    # Authentication is intentionally omitted; callers provide their user_id.
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id query parameter is required"}), 400
    try:
        response = (
            supabase.table("registrations")
            .select("id,user_id,event_id,created_at,events(*)")
            .eq("user_id", user_id)
            .execute()
        )
    except APIError as error:
        return database_error(error)
    return jsonify(response.data)


@app.get("/api/events/<event_id>/registrations")
def event_registrations(event_id):
    # This endpoint is intentionally unauthenticated as requested.
    try:
        event_result = (
            supabase.table("events")
            .select("id")
            .eq("id", event_id)
            .maybe_single()
            .execute()
        )
        if event_result.data is None:
            return jsonify({"error": "Event not found"}), 404
        response = (
            supabase.table("registrations")
            .select("id,user_id,event_id,created_at,users(id,email)")
            .eq("event_id", event_id)
            .execute()
        )
    except APIError as error:
        return database_error(error)
    return jsonify(response.data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
