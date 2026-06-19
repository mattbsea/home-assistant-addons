"""Append-only logging wrappers for outbound Tesla Fleet API calls.

Wraps the injectable HTTP primitives that ``prime.prime_once`` uses so every recurring Fleet call
(token refresh, ``/products``, ``/vehicle_data``) is appended to a durable JSONL log — a parallel to
the telemetry capture log — for correlating the 30-minute poll against the live telemetry stream.

OAuth secrets are redacted before anything is written; everything else (including the full
vehicle_data response) is kept verbatim so the prime payload can be compared field-by-field with
telemetry. The wrappers re-raise on error so the caller's own error handling is unchanged.
"""
import time

# Redacted anywhere they appear, in requests or responses, at any nesting depth.
_SECRET_KEYS = ("access_token", "refresh_token", "id_token", "client_secret", "code")


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in _SECRET_KEYS else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def wrap_get(log, get):
    """Wrap prime's GET (url, token) -> json, logging each call. `log` is a reclog.RecordLog or None."""
    if log is None:
        return get

    def logged_get(url, token):
        entry = {"ts": time.time(), "kind": "fleet_get", "url": url}
        try:
            resp = get(url, token)
            entry["ok"] = True
            entry["response"] = redact(resp)
            return resp
        except Exception as e:
            entry["ok"] = False
            entry["error"] = repr(e)
            raise
        finally:
            log.write(entry)
    return logged_get


def wrap_post_form(log, post_form):
    """Wrap prime's POST-form (url, data) -> json, logging each call. `log` is a RecordLog or None."""
    if log is None:
        return post_form

    def logged_post_form(url, data):
        entry = {"ts": time.time(), "kind": "fleet_post_form", "url": url, "request": redact(data)}
        try:
            resp = post_form(url, data)
            entry["ok"] = True
            entry["response"] = redact(resp)
            return resp
        except Exception as e:
            entry["ok"] = False
            entry["error"] = repr(e)
            raise
        finally:
            log.write(entry)
    return logged_post_form
