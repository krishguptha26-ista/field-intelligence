"""Adversarial regression tests for the failures found during assessment review.

These use the real FastAPI routes, real SQLAlchemy models, and deterministic
fixture provider. They are intentionally about trust boundaries rather than
happy-path response snapshots.
"""
from __future__ import annotations

import os
import json
from io import BytesIO
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch


TEST_DB = Path(__file__).resolve().parent.parent / "var" / "test_regressions.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["LLM_PROVIDER"] = "fixture"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_MAPS_API_KEY"] = ""
os.environ["ENABLE_CHALLENGE_PANEL"] = "true"
os.environ["APP_ENV"] = "testing"

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from server.app import app  # noqa: E402
from server import config  # noqa: E402
from server.agent import challenge  # noqa: E402
from server.models import (AuditSession, ClarificationQuestion, DemoAccessEvent, Finding, ModelCall, Observation,
                           OperationalTicket, SessionLocal, Standard, engine,
                           uid)  # noqa: E402
from server.agent.orchestrator import _scope_representative_standard  # noqa: E402
from server.gateway import GeminiProvider  # noqa: E402
from server.schemas import (ActionDraft, AnalysisResult, FindingDraft,
                            MediaDescription, ObservationDecision,
                            PhotoDescription)  # noqa: E402


class TrustBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()
        signed_in = cls.client.post("/api/auth/login", json={
            "username": "demo-user",
            "password": "Broadpeak-demo-user",
        })
        if signed_in.status_code != 200:
            raise RuntimeError(f"test login failed: {signed_in.text}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        engine.dispose()
        if TEST_DB.exists():
            try:
                TEST_DB.unlink()
            except PermissionError:
                pass

    def new_audit(self) -> str:
        response = self.client.post("/api/audits", json={
            "tenant_id": "broadpeak-demo",
            "location_id": "wolf-creek-atlanta",
            "consultant_name": "Regression Tester",
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def authenticated_client(self) -> TestClient:
        client = TestClient(app)
        signed_in = client.post("/api/auth/login", json={
            "username": "demo-user", "password": "Broadpeak-demo-user",
        })
        self.assertEqual(signed_in.status_code, 200, signed_in.text)
        return client

    def test_curated_showcase_is_the_only_visible_seeded_visit_and_is_immutable(self) -> None:
        visits = self.client.get("/api/audits", params={
            "tenant_id": "broadpeak-demo",
            "location_id": "wolf-creek-atlanta",
        })
        self.assertEqual(visits.status_code, 200, visits.text)
        showcase_rows = [row for row in visits.json() if row["is_showcase"]]
        self.assertEqual(len(showcase_rows), 1)
        self.assertEqual(showcase_rows[0]["id"], "audit_showcase_wolf_creek")
        self.assertEqual(showcase_rows[0]["status"], "SUBMITTED")
        self.assertEqual(showcase_rows[0]["checklist_responses"], 29)
        self.assertFalse(any(row["status"] == "SHOWCASE_SUPPORT" for row in visits.json()))

        packet = self.client.get("/api/audits/audit_showcase_wolf_creek")
        self.assertEqual(packet.status_code, 200, packet.text)
        data = packet.json()
        self.assertEqual(len(data["questions"]), 3)
        self.assertTrue(all(row["status"] == "ANSWERED" for row in data["questions"]))
        self.assertEqual(
            sorted(row["status"] for row in data["findings"]),
            ["APPROVED", "APPROVED", "REJECTED"],
        )
        self.assertEqual(len(data["field_tickets"]), 2)
        self.assertEqual(len(data["actions"]), 2)

        mutation = self.client.post(
            "/api/audits/audit_showcase_wolf_creek/observations",
            json={"kind": "NOTE", "text": "Must remain read-only", "zone_id": "z1_00"},
        )
        self.assertEqual(mutation.status_code, 409, mutation.text)

        tickets = self.client.get(
            "/api/locations/wolf-creek-atlanta/tickets").json()["tickets"]
        showcase_tickets = {row["id"]: row for row in tickets
                            if row["id"].startswith("ticket_showcase_")}
        self.assertEqual(showcase_tickets["ticket_showcase_security"]["status"],
                         "CLOSED_VERIFIED")
        self.assertEqual(showcase_tickets["ticket_showcase_restroom"]["status"],
                         "RESOLVED_PENDING_VERIFICATION")
        for row in showcase_tickets.values():
            self.assertEqual(len(row["before_evidence"]), 1)
            self.assertEqual(len(row["after_evidence"]), 1)
            for item in row["before_evidence"] + row["after_evidence"]:
                rendered = self.client.get(f"/api/photos/{item['digest']}")
                self.assertEqual(rendered.status_code, 200, rendered.text)
                self.assertEqual(rendered.headers["content-type"], "image/svg+xml")

        console = self.client.get("/api/console")
        self.assertEqual(console.status_code, 200, console.text)
        db = SessionLocal()
        try:
            visible_audit_count = (db.query(AuditSession)
                                   .filter(AuditSession.status != "SHOWCASE_SUPPORT")
                                   .count())
        finally:
            db.close()
        self.assertEqual(console.json()["totals"]["audits"], visible_audit_count)

    def test_shared_demo_login_protects_api_and_sets_browser_headers(self) -> None:
        with TestClient(app) as anonymous:
            denied = anonymous.get("/api/tenants")
            self.assertEqual(denied.status_code, 401, denied.text)
            self.assertEqual(denied.headers["x-frame-options"], "DENY")
            self.assertIn("frame-ancestors 'none'", denied.headers["content-security-policy"])
            self.assertEqual(denied.headers["cache-control"], "no-store")
            health = anonymous.get("/api/health")
            self.assertEqual(health.status_code, 200, health.text)
            active = anonymous.get("/api/active")
            self.assertEqual(active.status_code, 200, active.text)
            self.assertEqual(active.json(), {
                "ok": True,
                "service": "fieldintel",
                "purpose": "external_uptime_probe",
            })
            wrong = anonymous.post("/api/auth/login", json={
                "username": "demo-user", "password": "wrong",
            })
            self.assertEqual(wrong.status_code, 401, wrong.text)
            signed_in = anonymous.post("/api/auth/login", json={
                "username": "demo-user", "password": "Broadpeak-demo-user",
            })
            self.assertEqual(signed_in.status_code, 200, signed_in.text)
            self.assertIn("HttpOnly", signed_in.headers["set-cookie"])
            self.assertIn("SameSite=strict", signed_in.headers["set-cookie"])
            self.assertEqual(anonymous.get("/api/tenants").status_code, 200)

    def test_successful_login_is_recorded_and_notified_without_sensitive_data(self) -> None:
        delivered = MagicMock()
        delivered.raise_for_status.return_value = None
        db = SessionLocal()
        before = db.query(DemoAccessEvent).count()
        db.close()
        with (patch.object(config, "LOGIN_NOTIFICATION_WEBHOOK_URL",
                           "https://hook.example.invalid/login"),
              patch("server.app.httpx.post", return_value=delivered) as webhook,
              TestClient(app) as client):
            wrong = client.post("/api/auth/login", json={
                "username": "demo-user", "password": "wrong",
            })
            self.assertEqual(wrong.status_code, 401, wrong.text)
            signed_in = client.post("/api/auth/login", json={
                "username": "demo-user", "password": "Broadpeak-demo-user",
            }, headers={
                "x-forwarded-for": "203.0.113.40",
                "user-agent": "Login notification regression",
            })
            self.assertEqual(signed_in.status_code, 200, signed_in.text)
            console = client.get("/api/console")
            self.assertEqual(console.status_code, 200, console.text)
            self.assertTrue(console.json()["access_activity"]["webhook_configured"])

        webhook.assert_called_once()
        payload = webhook.call_args.kwargs["json"]
        serialised = json.dumps(payload)
        self.assertNotIn("Broadpeak-demo-user", serialised)
        self.assertNotIn("203.0.113.40", serialised)
        self.assertEqual(payload["event"], "FIELDINTEL_DEMO_LOGIN")

        db = SessionLocal()
        try:
            self.assertEqual(db.query(DemoAccessEvent).count(), before + 1)
            event = (db.query(DemoAccessEvent)
                     .order_by(DemoAccessEvent.created_at.desc()).first())
            self.assertEqual(event.notification_status, "SENT")
            self.assertEqual(event.user_agent, "Login notification regression")
            self.assertEqual(len(event.client_fingerprint), 12)
        finally:
            db.close()

    def test_login_succeeds_when_notification_delivery_fails(self) -> None:
        with (patch.object(config, "LOGIN_NOTIFICATION_WEBHOOK_URL",
                           "https://hook.example.invalid/login"),
              patch("server.app.httpx.post", side_effect=RuntimeError("offline")),
              TestClient(app) as client):
            signed_in = client.post("/api/auth/login", json={
                "username": "demo-user", "password": "Broadpeak-demo-user",
            })
            self.assertEqual(signed_in.status_code, 200, signed_in.text)
            self.assertEqual(client.get("/api/tenants").status_code, 200)

        db = SessionLocal()
        try:
            event = (db.query(DemoAccessEvent)
                     .order_by(DemoAccessEvent.created_at.desc()).first())
            self.assertEqual(event.notification_status, "FAILED")
            self.assertEqual(event.detail.get("delivery_error"), "RuntimeError")
        finally:
            db.close()

    def test_photo_for_one_check_cannot_support_another_check_in_same_zone(self) -> None:
        audit_id = self.new_audit()
        guide = self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()
        arrival = next(zone for zone in guide["zones"]
                       if zone["name"] == "Arrival & entrance signage")
        photo_id = self.upload_photo(
            audit_id, zone_id=arrival["id"], standard_code="SIG-01")
        mismatched = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{
                "item": "Walkway check", "standard_code": "OSHA-WALK-01",
                "response": "FAIL", "detail": "Trip hazard at the entrance curb",
                "zone_id": arrival["id"],
                "evidence_observation_ids": [photo_id],
            }],
        })
        self.assertEqual(mismatched.status_code, 422, mismatched.text)
        self.assertIn("this exact issue", mismatched.text)

    def test_concurrent_checklist_saves_do_not_lose_a_zone(self) -> None:
        audit_id = self.new_audit()
        guide = self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()
        selected = []
        for zone in guide["zones"]:
            ordinary = next((check for check in zone["checks"]
                             if not check.get("authority_type")), None)
            if ordinary:
                selected.append((zone, ordinary))
            if len(selected) == 2:
                break
        clients = [self.authenticated_client(), self.authenticated_client()]
        try:
            def save(index: int):
                zone, check = selected[index]
                return clients[index].post(f"/api/audits/{audit_id}/checklist", json={
                    "responses": [{
                        "item": check["question"],
                        "standard_code": check["standard_code"],
                        "response": "PASS", "detail": "", "zone_id": zone["id"],
                        "evidence_observation_ids": [],
                    }],
                })
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(save, range(2)))
            self.assertTrue(all(row.status_code == 200 for row in responses),
                            [row.text for row in responses])
        finally:
            for client in clients:
                client.close()
        saved = self.client.get(f"/api/audits/{audit_id}").json()["checklist_responses"]
        keys = {(row["zone_id"], row["standard_code"]) for row in saved}
        self.assertTrue(all((zone["id"], check["standard_code"]) in keys
                            for zone, check in selected), saved)

    def test_concurrent_analysis_is_idempotent_and_budget_atomic(self) -> None:
        audit_id = self.new_audit()
        observed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "text": "The entrance looked a little unusual.",
        })
        self.assertEqual(observed.status_code, 200, observed.text)
        clients = [self.authenticated_client() for _ in range(6)]
        try:
            with patch("server.config.MAX_LLM_CALLS_PER_AUDIT", 1):
                with ThreadPoolExecutor(max_workers=6) as pool:
                    responses = list(pool.map(
                        lambda client: client.post(f"/api/audits/{audit_id}/analyze"),
                        clients,
                    ))
            self.assertTrue(all(row.status_code == 429 for row in responses),
                            [(row.status_code, row.text) for row in responses])
        finally:
            for client in clients:
                client.close()
        db = SessionLocal()
        calls = db.query(ModelCall).filter_by(audit_id=audit_id).count()
        db.close()
        self.assertEqual(calls, 1)

        second_audit = self.new_audit()
        observed = self.client.post(f"/api/audits/{second_audit}/observations", json={
            "kind": "NOTE", "text": "The entrance looked a little unusual.",
        })
        self.assertEqual(observed.status_code, 200, observed.text)
        clients = [self.authenticated_client() for _ in range(4)]
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(
                    lambda client: client.post(f"/api/audits/{second_audit}/analyze"),
                    clients,
                ))
            self.assertTrue(all(row.status_code == 200 for row in responses),
                            [(row.status_code, row.text) for row in responses])
        finally:
            for client in clients:
                client.close()
        db = SessionLocal()
        calls = db.query(ModelCall).filter_by(audit_id=second_audit).count()
        open_questions = db.query(ClarificationQuestion).filter_by(
            audit_id=second_audit, status="OPEN").count()
        db.close()
        self.assertEqual(calls, 2)
        self.assertEqual(open_questions, 1)

    def test_ticket_photo_metadata_is_removed_before_storage(self) -> None:
        ticket_id = uid("ticket")
        db = SessionLocal()
        db.add(OperationalTicket(
            id=ticket_id, tenant_id="broadpeak-demo",
            location_id="wolf-creek-atlanta", dedupe_key=uid("dedupe"),
            source_kind="TEST", source_refs=[], category="safety",
            title="Metadata stripping test", description="Test evidence",
            priority="LOW", assigned_role="Location Manager",
            status="PENDING_VALIDATION", validity_status="UNASSESSED",
            due_date="2026-08-17", before_evidence=[], after_evidence=[],
            external_reply={}, events=[],
        ))
        db.commit()
        db.close()
        image = Image.new("RGB", (32, 32), "green")
        exif = Image.Exif()
        exif[270] = "PRIVATE LOCATION NOTE"
        buffer = BytesIO()
        image.save(buffer, format="JPEG", exif=exif)
        uploaded = self.client.post(
            f"/api/tickets/{ticket_id}/evidence",
            data={"stage": "BEFORE", "note": "Entrance before image",
                  "actor": "Location Operator"},
            files={"file": ("before.jpg", buffer.getvalue(), "image/jpeg")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        digest = uploaded.json()["evidence"]["digest"]
        stored = self.client.get(f"/api/photos/{digest}")
        self.assertEqual(stored.status_code, 200, stored.text)
        with Image.open(BytesIO(stored.content)) as canonical:
            self.assertEqual(dict(canonical.getexif()), {})

    def upload_photo(self, audit_id: str, *, observation_id: str | None = None,
                     zone_id: str | None = None,
                     standard_code: str | None = None) -> str:
        buffer = BytesIO()
        Image.new("RGB", (32, 32), "blue").save(buffer, format="PNG")
        data = {"privacy_attested": "true"}
        if observation_id:
            data["supports_observation_id"] = observation_id
        if zone_id:
            data["zone_id"] = zone_id
        if standard_code:
            data["evidence_for_standard_code"] = standard_code
        response = self.client.post(
            f"/api/audits/{audit_id}/photo", data=data,
            files={"file": ("evidence.png", buffer.getvalue(), "image/png")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["accepted"])
        return response.json()["observation_id"]

    def create_finding(self) -> tuple[str, str]:
        audit_id = self.new_audit()
        observed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE",
            "text": ("Men's clubhouse restroom waste bin overflowing with standing "
                     "water around the second sink."),
        })
        self.assertEqual(observed.status_code, 200, observed.text)
        observation_id = observed.json()["id"]
        requested = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(requested.status_code, 200, requested.text)
        self.assertTrue(
            requested.json().get("evidence_requests")
            or requested.json().get("evidence_recommendations"),
            requested.text,
        )
        self.upload_photo(audit_id, observation_id=observation_id)
        deferred = {"ran": False, "reason": "test defers panel to review",
                    "challenges": [], "outcome": "DEFERRED_TO_REVIEW"}
        with patch("server.agent.orchestrator.challenge.run_panel",
                   return_value=deferred):
            analysed = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(analysed.status_code, 200, analysed.text)
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        self.assertEqual(len(audit["findings"]), 1, audit)
        return audit_id, audit["findings"][0]["id"]

    def create_deferred_finding(self) -> tuple[str, str]:
        deferred = {
            "ran": False,
            "reason": "deferred to independent review for field latency",
            "challenges": [],
            "outcome": "DEFERRED_TO_REVIEW",
        }
        with patch("server.agent.orchestrator.challenge.run_panel",
                   return_value=deferred):
            return self.create_finding()

    def complete_required_checks(self, audit_id: str, *, fail_first: bool = False) -> None:
        guide = self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()
        responses = []
        failed = False
        failure_photo_id = None
        for zone in guide["zones"]:
            if not zone["required"]:
                continue
            for check in zone["checks"]:
                should_fail = fail_first and not failed
                if should_fail:
                    failure_photo_id = self.upload_photo(
                        audit_id, zone_id=zone["id"],
                        standard_code=check["standard_code"])
                responses.append({
                    "item": check["question"],
                    "standard_code": check["standard_code"],
                    "response": "FAIL" if should_fail else "PASS",
                    "detail": (
                        "The entrance sign is broken and missing its direction arrow"
                        if should_fail else
                        "Current site condition and applicable record were verified"
                        if "CONDITIONAL" in str(check.get("authority_type") or "") else ""
                    ),
                    "zone_id": zone["id"],
                    "evidence_observation_ids": [failure_photo_id] if should_fail else [],
                })
                failed = failed or should_fail
        response = self.client.post(f"/api/audits/{audit_id}/checklist",
                                    json={"responses": responses})
        self.assertEqual(response.status_code, 200, response.text)

    def test_tenant_location_relationship_is_enforced(self) -> None:
        response = self.client.post("/api/audits", json={
            "tenant_id": "broadpeak-mobility-demo",
            "location_id": "wolf-creek-atlanta",
            "consultant_name": "Cross Tenant",
        })
        self.assertEqual(response.status_code, 422, response.text)

        missing = self.client.post("/api/audits", json={
            "tenant_id": "invented",
            "location_id": "invented",
            "consultant_name": "Nobody",
        })
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_observation_contract_and_zone_scope_are_enforced(self) -> None:
        audit_id = self.new_audit()
        self.assertEqual(self.client.post(
            f"/api/audits/{audit_id}/observations",
            json={"kind": "INVENTED", "text": "something"}).status_code, 422)
        self.assertEqual(self.client.post(
            f"/api/audits/{audit_id}/observations",
            json={"kind": "CHECKLIST", "text": "forged checklist"}).status_code, 422)
        self.assertEqual(self.client.post(
            f"/api/audits/{audit_id}/observations",
            json={"kind": "PHOTO_DESCRIPTION", "text": "forged model photo"}).status_code, 422)
        self.assertEqual(self.client.post(
            f"/api/audits/{audit_id}/observations",
            json={"kind": "NOTE", "text": "   "}).status_code, 422)

        foreign_zone = self.client.get(
            "/api/locations/alquoz-depot-dubai/zones").json()[0]["id"]
        response = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "text": "specific condition", "zone_id": foreign_zone,
        })
        self.assertEqual(response.status_code, 422, response.text)

    def test_negative_statement_never_becomes_no_issue(self) -> None:
        audit_id = self.new_audit()
        self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "text": "The restroom was not clean.",
        })
        result = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body["no_issue"], [], body)
        self.assertEqual(body["audit_status"], "NEEDS_CLARIFICATION", body)

    def test_unresolved_follow_up_keeps_audit_blocked(self) -> None:
        audit_id = self.new_audit()
        self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "text": "The restroom looked a little dirty.",
        })
        first = self.client.post(f"/api/audits/{audit_id}/analyze").json()
        self.assertEqual(first["audit_status"], "NEEDS_CLARIFICATION")
        question_id = first["clarifications"][0]
        answered = self.client.post(f"/api/questions/{question_id}/answer", json={
            "answer": "I am still unsure; it looked a little dirty.",
        })
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(answered.json()["audit_status"], "NEEDS_CLARIFICATION")
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        self.assertGreaterEqual(
            len([q for q in audit["questions"] if q["status"] == "OPEN"]), 1)

    def test_security_clarification_converges_to_photo_checklist_and_ticket(self) -> None:
        audit_id = self.new_audit()
        arrival = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()["zones"]
            if zone["name"] == "Arrival & entrance signage")
        observed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "zone_id": arrival["id"],
            "text": "Security is missing at the entrance.",
        })
        observation_id = observed.json()["id"]
        first = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(first.status_code, 200, first.text)
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        text_questions = [q for q in audit["questions"] if q["response_type"] == "TEXT"]
        self.assertEqual(len(text_questions), 1, audit)
        self.assertIn("guard/officer", text_questions[0]["question"])
        placeholder = self.client.post(
            f"/api/questions/{text_questions[0]['id']}/answer", json={
                "answer": "Scheduled from [start time] to [end time]",
            })
        self.assertEqual(placeholder.status_code, 409, placeholder.text)
        self.assertEqual(next(q for q in self.client.get(
            f"/api/audits/{audit_id}").json()["questions"]
            if q["id"] == text_questions[0]["id"])["status"], "OPEN")

        answer = "The scheduled security guard was absent; I do not mean equipment."
        answered = self.client.post(
            f"/api/questions/{text_questions[0]['id']}/answer", json={"answer": answer})
        self.assertEqual(answered.status_code, 200, answered.text)
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        open_questions = [q for q in audit["questions"] if q["status"] == "OPEN"]
        self.assertEqual(len(open_questions), 1, audit)
        self.assertEqual(open_questions[0]["response_type"], "PHOTO_RECOMMENDED")
        self.assertEqual(len([q for q in audit["questions"]
                              if q["response_type"] == "TEXT"]), 1)

        db = SessionLocal()
        try:
            calls_before_retry = db.query(ModelCall).filter_by(audit_id=audit_id).count()
        finally:
            db.close()
        retry = self.client.post(
            f"/api/questions/{text_questions[0]['id']}/answer", json={"answer": answer})
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertTrue(retry.json()["idempotent"])
        db = SessionLocal()
        try:
            self.assertEqual(db.query(ModelCall).filter_by(audit_id=audit_id).count(),
                             calls_before_retry)
        finally:
            db.close()

        self.upload_photo(audit_id, observation_id=observation_id, zone_id=arrival["id"])
        deferred = {"ran": False, "reason": "test defers panel to review",
                    "challenges": [], "outcome": "DEFERRED_TO_REVIEW"}
        with patch("server.agent.orchestrator.challenge.run_panel",
                   return_value=deferred):
            completed = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(completed.status_code, 200, completed.text)
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        self.assertFalse([q for q in audit["questions"] if q["status"] == "OPEN"], audit)
        self.assertEqual(len(audit["findings"]), 1, audit)
        finding = audit["findings"][0]
        self.assertEqual(finding["standard"]["code"], "SEC-01")
        self.assertIn(answer, finding["consultant_statement"])
        self.assertIsNotNone(finding["ticket"])
        self.assertEqual(finding["ticket"]["status"], "PENDING_VALIDATION")
        self.assertEqual(len(finding["ticket"]["before_evidence"]), 1)
        supporting = next(observation for observation in audit["observations"]
                          if observation["payload"].get("supports_observation_id") == observation_id)
        self.assertEqual(supporting["provenance"], "PHOTO_CAPTURED_UNDESCRIBED")
        self.assertTrue(supporting["payload"]["requires_manual_review"])
        security_check = next(row for row in audit["checklist_responses"]
                              if row["standard_code"] == "SEC-01")
        self.assertEqual(security_check["response"], "FAIL")
        self.assertTrue(security_check["auto_reconciled"])
        self.assertEqual(
            security_check["verification_state"], "PHOTO_ATTACHED_PENDING_REVIEW")
        reviewed = self.client.post(f"/api/findings/{finding['id']}/review", json={
            "action": "approve", "reviewer": "Independent Operations Reviewer",
            "reason": "Photo-attached field report and schedule requirement reviewed",
        })
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()["ticket_id"], finding["ticket"]["id"])
        linked = self.client.get(f"/api/audits/{audit_id}").json()["field_tickets"][0]
        self.assertEqual(linked["status"], "OPEN")
        self.assertEqual(linked["validity_status"], "VALIDATED_BY_FINDING_REVIEW")
        self.assertIn(reviewed.json()["action_id"], linked["source_refs"])

    def test_auto_reconciled_checklist_resubmission_cannot_duplicate_incident(self) -> None:
        """Free report -> photo -> reconciliation -> zone save stays one incident."""
        audit_id = self.new_audit()
        guide = self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()["zones"]
        arrival = next(zone for zone in guide
                       if zone["name"] == "Arrival & entrance signage")
        other_zone = next(zone for zone in guide
                          if zone["id"] != arrival["id"] and zone["checks"])
        observed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "zone_id": arrival["id"],
            "text": "Security is missing at the entrance.",
        }).json()
        self.client.post(f"/api/audits/{audit_id}/analyze")
        question = next(q for q in self.client.get(
            f"/api/audits/{audit_id}").json()["questions"] if q["status"] == "OPEN")
        self.client.post(f"/api/questions/{question['id']}/answer", json={
            "answer": "The scheduled security guard was absent; I do not mean equipment.",
        })
        photo_id = self.upload_photo(
            audit_id, observation_id=observed["id"], zone_id=arrival["id"])
        deferred = {"ran": False, "reason": "test defers panel to review",
                    "challenges": [], "outcome": "DEFERRED_TO_REVIEW"}
        with patch("server.agent.orchestrator.challenge.run_panel",
                   return_value=deferred):
            self.client.post(f"/api/audits/{audit_id}/analyze")
        before = self.client.get(f"/api/audits/{audit_id}").json()
        security = next(row for row in before["checklist_responses"]
                        if row["standard_code"] == "SEC-01")
        other = other_zone["checks"][0]

        saved = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [
                {"item": security["item"], "standard_code": "SEC-01",
                 "response": "FAIL", "detail": security["detail"],
                 "zone_id": arrival["id"],
                 "evidence_observation_ids": security["evidence_observation_ids"]},
                {"item": other["question"],
                 "standard_code": other["standard_code"], "response": "PASS",
                 "detail": "Condition observed as acceptable during this visit.",
                 "zone_id": other_zone["id"], "evidence_observation_ids": []},
            ],
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        self.client.post(f"/api/audits/{audit_id}/analyze")
        after = self.client.get(f"/api/audits/{audit_id}").json()
        self.assertEqual(len(after["findings"]), 1, after)
        self.assertEqual(len(after["field_tickets"]), 1, after)
        self.assertFalse(any(
            row["kind"] == "CHECKLIST"
            and row["payload"].get("standard_code") == "SEC-01"
            for row in after["observations"]), after)
        saved_security = next(row for row in after["checklist_responses"]
                              if row["standard_code"] == "SEC-01")
        self.assertEqual(saved_security["originating_finding_id"],
                         before["findings"][0]["id"])

        # Service-level defence: even a legacy/forged duplicate checklist
        # observation with the same standard and photo cannot create a packet.
        db = SessionLocal()
        try:
            db.add(Observation(
                id=uid("ob"), tenant_id="broadpeak-demo", audit_id=audit_id,
                kind="CHECKLIST", zone_id=arrival["id"],
                provenance="CONSULTANT_OBSERVATION",
                text=("Checklist item failed: scheduled entrance security coverage — "
                      "The scheduled security guard was absent."),
                payload={"standard_code": "SEC-01", "response": "FAIL",
                         "evidence_observation_ids": [photo_id]},
            ))
            db.commit()
        finally:
            db.close()
        deduped = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(deduped.status_code, 200, deduped.text)
        self.assertTrue(deduped.json().get("deduplicated_findings"), deduped.text)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Finding).filter_by(audit_id=audit_id).count(), 1)
            self.assertEqual(db.query(OperationalTicket).filter(
                OperationalTicket.source_kind == "PHOTO_BACKED_FIELD_FINDING",
                OperationalTicket.location_id == "wolf-creek-atlanta",
            ).filter(OperationalTicket.source_refs.contains(
                before["findings"][0]["id"])).count(), 1)
        finally:
            db.close()

    def test_sqlite_capture_challenge_does_not_self_lock(self) -> None:
        """The panel ledger must not contend with the analysis transaction."""
        audit_id = self.new_audit()
        observed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE",
            "text": ("Men's clubhouse restroom waste bin overflowing with standing "
                     "water around the second sink and a strong odour."),
        })
        self.assertEqual(observed.status_code, 200, observed.text)
        requested = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(requested.status_code, 200, requested.text)
        self.upload_photo(audit_id, observation_id=observed.json()["id"])

        analysed = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(analysed.status_code, 200, analysed.text)
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        self.assertEqual(len(audit["findings"]), 1, audit)
        panel = audit["findings"][0]["challenge_record"]
        self.assertTrue(panel["ran"], panel)
        self.assertEqual(panel["votes"]["abstain"], 0, panel)

    def test_photo_question_rejects_a_text_answer(self) -> None:
        audit_id = self.new_audit()
        observed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE",
            "zone_id": "z1_00",
            "text": "A loose electrical cable is stretched across the entrance walkway as a trip hazard.",
        })
        observation_id = observed.json()["id"]
        requested = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(requested.status_code, 200, requested.text)
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        photo_question = next(q for q in audit["questions"]
                              if q["observation_id"] == observation_id
                              and q["response_type"] == "PHOTO")
        forged = self.client.post(f"/api/questions/{photo_question['id']}/answer", json={
            "answer": "Yes, I will attach it later",
        })
        self.assertEqual(forged.status_code, 409, forged.text)
        still_open = self.client.get(f"/api/audits/{audit_id}").json()["questions"]
        self.assertEqual(next(q for q in still_open if q["id"] == photo_question["id"])[
            "status"], "OPEN")

    def test_auto_reconciliation_preserves_pass_until_consultant_confirms(self) -> None:
        audit_id = self.new_audit()
        arrival = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()["zones"]
            if zone["name"] == "Arrival & entrance signage")
        saved = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{"item": "Security coverage", "standard_code": "SEC-01",
                           "response": "PASS", "detail": "Scheduled guard present",
                           "zone_id": arrival["id"]}],
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        observed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "zone_id": arrival["id"],
            "text": ("The scheduled entrance security guard is absent during opening "
                     "hours; the equipment is present."),
        })
        observation_id = observed.json()["id"]
        self.client.post(f"/api/audits/{audit_id}/analyze")
        photo_id = self.upload_photo(
            audit_id, observation_id=observation_id, zone_id=arrival["id"])
        deferred = {"ran": False, "reason": "test", "challenges": [],
                    "outcome": "DEFERRED_TO_REVIEW"}
        with patch("server.agent.orchestrator.challenge.run_panel",
                   return_value=deferred):
            analysed = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertTrue(analysed.json()["findings"], analysed.text)
        state = self.client.get(f"/api/audits/{audit_id}").json()
        conflict = next(row for row in state["checklist_responses"]
                        if row["standard_code"] == "SEC-01")
        self.assertEqual(conflict["response"], "PASS")
        self.assertEqual(conflict["detail"], "Scheduled guard present")
        self.assertEqual(conflict["reconciliation_conflict"]["suggested_response"], "FAIL")
        confirmed = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{"item": "Security coverage", "standard_code": "SEC-01",
                           "response": "FAIL",
                           "detail": "Scheduled entrance guard absent during opening hours",
                           "zone_id": arrival["id"],
                           "evidence_observation_ids": [photo_id]}],
        })
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        resolved = next(row for row in self.client.get(f"/api/audits/{audit_id}").json()[
            "checklist_responses"] if row["standard_code"] == "SEC-01")
        self.assertEqual(resolved["response"], "FAIL")
        self.assertNotIn("reconciliation_conflict", resolved)
        self.assertEqual(resolved["conflict_resolution"], "CONSULTANT_CONFIRMED_ISSUE")

    def test_unmapped_concern_stops_repeating_and_routes_without_compliance_claim(self) -> None:
        audit_id = self.new_audit()
        observed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "text": "Ornamental fountain controller displays ZQ-19 fault.",
        })
        observation_id = observed.json()["id"]
        first = self.client.post(f"/api/audits/{audit_id}/analyze").json()
        qid = first["clarifications"][0]
        second = self.client.post(f"/api/questions/{qid}/answer", json={
            "answer": "Controller beside the entrance fountain; ZQ-19 remains on screen.",
        })
        self.assertEqual(second.status_code, 200, second.text)
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        open_questions = [q for q in audit["questions"] if q["status"] == "OPEN"]
        self.assertEqual(len(open_questions), 1, audit)
        self.assertEqual(open_questions[0]["response_type"], "PHOTO")
        self.assertIn("without another repetitive question", open_questions[0]["question"])
        uploaded_id = self.upload_photo(audit_id, observation_id=observation_id)
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        self.assertFalse([q for q in audit["questions"] if q["status"] == "OPEN"], audit)
        self.assertEqual(audit["findings"], [])
        self.assertEqual(len(audit["field_tickets"]), 1)
        ticket = audit["field_tickets"][0]
        self.assertEqual(ticket["source_kind"], "UNMAPPED_PHOTO_BACKED_FIELD_CONCERN")
        self.assertIn(uploaded_id, ticket["source_refs"])
        self.assertIn("no controlled standard", ticket["description"].lower())

    def test_checklist_issue_requires_an_explicit_photo(self) -> None:
        audit_id = self.new_audit()
        arrival = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()["zones"]
            if zone["name"] == "Arrival & entrance signage")
        response = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{"item": "Walking surface", "standard_code": "OSHA-WALK-01",
                           "response": "FAIL", "detail": "Cable across entrance walkway",
                           "zone_id": arrival["id"], "evidence_observation_ids": []}],
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("linked photo", response.text)

    def test_checklist_recommended_photo_can_be_explicitly_skipped(self) -> None:
        audit_id = self.new_audit()
        restroom = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()["zones"]
            if zone["name"] == "Restrooms")
        payload = {
            "responses": [{
                "item": "Restroom condition",
                "standard_code": "CLN-01",
                "response": "FAIL",
                "detail": "Standing water at the second sink at 2:05pm",
                "zone_id": restroom["id"],
                "evidence_observation_ids": [],
            }],
        }
        undecided = self.client.post(f"/api/audits/{audit_id}/checklist", json=payload)
        self.assertEqual(undecided.status_code, 422, undecided.text)
        self.assertIn("explicitly continue", undecided.text)

        payload["responses"][0]["photo_decision"] = "CONTINUE_WITHOUT_PHOTO"
        saved = self.client.post(f"/api/audits/{audit_id}/checklist", json=payload)
        self.assertEqual(saved.status_code, 200, saved.text)
        row = next(
            item for item in self.client.get(f"/api/audits/{audit_id}").json()[
                "checklist_responses"]
            if item["standard_code"] == "CLN-01"
        )
        self.assertEqual(row["photo_policy"]["level"], "RECOMMENDED")
        self.assertEqual(row["photo_decision"], "CONTINUE_WITHOUT_PHOTO")
        self.assertEqual(
            row["verification_state"], "CONSULTANT_REPORTED_PHOTO_RECOMMENDED")

    def test_text_finding_can_continue_without_recommended_photo(self) -> None:
        audit_id = self.new_audit()
        observed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE",
            "text": ("Men's clubhouse restroom waste bin overflowing with standing "
                     "water around the second sink at 2:05pm."),
        })
        self.assertEqual(observed.status_code, 200, observed.text)
        requested = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(requested.status_code, 200, requested.text)
        question = next(
            row for row in self.client.get(f"/api/audits/{audit_id}").json()["questions"]
            if row["observation_id"] == observed.json()["id"]
            and row["response_type"] == "PHOTO_RECOMMENDED"
        )
        continued = self.client.post(f"/api/questions/{question['id']}/answer", json={
            "answer": "Continue without photo",
        })
        self.assertEqual(continued.status_code, 200, continued.text)
        state = self.client.get(f"/api/audits/{audit_id}").json()
        self.assertEqual(len(state["findings"]), 1, state)
        self.assertEqual(state["field_tickets"], [], state)
        self.assertLessEqual(state["findings"][0]["confidence"], 0.70)
        self.assertTrue(any(
            "lower evidence confidence" in reason.lower()
            for reason in state["findings"][0]["uncertainty_reasons"]
        ), state["findings"][0])

    def test_partial_zone_save_does_not_revalidate_legacy_wrong_zone_row(self) -> None:
        audit_id = self.new_audit()
        arrival = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()["zones"]
            if zone["name"] == "Arrival & entrance signage")
        db = SessionLocal()
        try:
            audit = db.get(AuditSession, audit_id)
            audit.checklist_responses = [{
                "item": "Legacy safety row",
                "standard_code": "SAF-01",
                "response": "PASS",
                "detail": "Persisted by an older build",
                "zone_id": arrival["id"],
            }]
            db.commit()
        finally:
            db.close()

        saved = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{
                "item": "Entrance signage",
                "standard_code": "SIG-01",
                "response": "PASS",
                "detail": "Current entrance sign is present and legible",
                "zone_id": arrival["id"],
            }],
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        rows = self.client.get(f"/api/audits/{audit_id}").json()["checklist_responses"]
        self.assertEqual({row["standard_code"] for row in rows}, {"SAF-01", "SIG-01"})

    def test_recent_visits_can_discard_drafts_but_not_submitted_packets(self) -> None:
        draft_id = self.new_audit()
        recent = self.client.get(
            "/api/audits?tenant_id=broadpeak-demo&location_id=wolf-creek-atlanta")
        self.assertEqual(recent.status_code, 200, recent.text)
        listed = next(row for row in recent.json() if row["id"] == draft_id)
        self.assertTrue(listed["can_discard"])

        mismatch = self.client.request("DELETE", f"/api/audits/{draft_id}", json={
            "confirm_audit_id": "a-different-visit",
            "requested_by": "Regression Tester",
        })
        self.assertEqual(mismatch.status_code, 422, mismatch.text)
        discarded = self.client.request("DELETE", f"/api/audits/{draft_id}", json={
            "confirm_audit_id": draft_id,
            "requested_by": "Regression Tester",
        })
        self.assertEqual(discarded.status_code, 200, discarded.text)
        self.assertEqual(discarded.json()["discarded"], draft_id)
        self.assertEqual(self.client.get(f"/api/audits/{draft_id}").status_code, 404)

        submitted_id = self.new_audit()
        db = SessionLocal()
        try:
            submitted = db.get(AuditSession, submitted_id)
            submitted.status = "SUBMITTED"
            db.commit()
        finally:
            db.close()
        immutable = self.client.request("DELETE", f"/api/audits/{submitted_id}", json={
            "confirm_audit_id": submitted_id,
            "requested_by": "Regression Tester",
        })
        self.assertEqual(immutable.status_code, 409, immutable.text)

    def test_checklist_cannot_silently_overwrite_an_existing_review_packet(self) -> None:
        audit_id = self.new_audit()
        restroom = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()["zones"]
            if zone["name"] == "Restrooms")
        photo_id = self.upload_photo(
            audit_id, zone_id=restroom["id"], standard_code="CLN-01")
        failed = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{"item": "Restroom condition", "standard_code": "CLN-01",
                           "response": "FAIL", "detail": "Standing water at sink",
                           "zone_id": restroom["id"],
                           "evidence_observation_ids": [photo_id]}],
        })
        self.assertEqual(failed.status_code, 200, failed.text)
        deferred = {"ran": False, "reason": "test defers panel to review",
                    "challenges": [], "outcome": "DEFERRED_TO_REVIEW"}
        with patch("server.agent.orchestrator.challenge.run_panel",
                   return_value=deferred):
            analysed = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertTrue(analysed.json()["findings"], analysed.text)
        shortened = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{"item": "Restroom condition", "standard_code": "CLN-01",
                           "response": "FAIL", "detail": "Standing water",
                           "zone_id": restroom["id"],
                           "evidence_observation_ids": [photo_id]}],
        })
        self.assertEqual(shortened.status_code, 409, shortened.text)
        overwrite = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{"item": "Restroom condition", "standard_code": "CLN-01",
                           "response": "PASS", "detail": "",
                           "zone_id": restroom["id"], "evidence_observation_ids": []}],
        })
        self.assertEqual(overwrite.status_code, 409, overwrite.text)
        saved = next(row for row in self.client.get(f"/api/audits/{audit_id}").json()[
            "checklist_responses"] if row["standard_code"] == "CLN-01")
        self.assertEqual(saved["response"], "FAIL")

    def test_checklist_submission_is_typed_and_idempotent(self) -> None:
        audit_id = self.new_audit()
        restroom = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()["zones"]
            if zone["name"] == "Restrooms")
        photo_id = self.upload_photo(
            audit_id, zone_id=restroom["id"], standard_code="CLN-01")
        payload = {"responses": [{
            "item": "Restroom surfaces clean",
            "standard_code": "CLN-01",
            "response": "fail",
            "detail": "Standing water at second sink",
            "zone_id": restroom["id"],
            "evidence_observation_ids": [photo_id],
        }]}
        first = self.client.post(f"/api/audits/{audit_id}/checklist", json=payload)
        second = self.client.post(f"/api/audits/{audit_id}/checklist", json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(len(first.json()["observations_created"]), 1)
        self.assertEqual(second.json()["observations_created"], [])
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Observation).filter_by(
                audit_id=audit_id, kind="CHECKLIST").count(), 1)
        finally:
            db.close()

    def test_field_guide_is_server_owned_sourced_and_non_adjudicative(self) -> None:
        guide = self.client.get("/api/locations/wolf-creek-atlanta/field-guide")
        self.assertEqual(guide.status_code, 200, guide.text)
        body = guide.json()
        self.assertEqual(body["authority"], "MIXED_SOURCED_GUIDANCE")
        self.assertEqual(body["jurisdiction"]["display"],
                         "City of South Fulton, Fulton County, Georgia")
        restroom = next(zone for zone in body["zones"] if zone["name"] == "Restrooms")
        self.assertEqual({check["standard_code"] for check in restroom["checks"]},
                         {"CLN-01", "ADA-GOLF-01"})
        self.assertTrue(all(check["authoritative"] is False for check in restroom["checks"]))
        ada = next(check for check in restroom["checks"]
                   if check["standard_code"] == "ADA-GOLF-01")
        self.assertTrue(ada["authoritative_source"])
        self.assertIn("ada.gov", ada["source_url"])
        self.assertIn("accessibility specialist", ada["applicability"].lower())

        audit_id = self.new_audit()
        forged = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{"item": "Invented rule", "standard_code": "FAKE-99",
                           "response": "FAIL", "detail": "Specific visible issue"}],
        })
        self.assertEqual(forged.status_code, 422, forged.text)

        parking = next(zone for zone in body["zones"]
                       if zone["name"] == "Parking / accessible parking")
        wrong_zone = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{"item": "Food surfaces", "standard_code": "FNB-01",
                           "response": "FAIL", "detail": "Food-contact surface is dirty",
                           "zone_id": parking["id"]}],
        })
        self.assertEqual(wrong_zone.status_code, 422, wrong_zone.text)
        self.assertIn("not applicable", wrong_zone.text)

    def test_model_budget_pause_has_visible_audited_recovery(self) -> None:
        audit_id = self.new_audit()
        with patch("server.config.MAX_LLM_CALLS_PER_AUDIT", 0):
            paused = self.client.post(f"/api/audits/{audit_id}/analyze")
            self.assertEqual(paused.status_code, 429, paused.text)
            budget = self.client.get(f"/api/audits/{audit_id}/budget")
            self.assertEqual(budget.status_code, 200, budget.text)
            self.assertEqual(budget.json()["remaining_calls"], 0)
            self.assertTrue(budget.json()["can_acknowledge"])

            continued = self.client.post(
                f"/api/audits/{audit_id}/budget/acknowledge",
                json={
                    "acknowledged_by": "Regression Tester",
                    "reason": "Finish the active visit after reviewing model-call usage.",
                    "request_id": "regression-budget-ack-001",
                },
            )
            self.assertEqual(continued.status_code, 200, continued.text)
            self.assertEqual(continued.json()["remaining_calls"],
                             continued.json()["extension_calls"])
            self.assertEqual(continued.json()["acknowledgements"], 1)
            repeated = self.client.post(
                f"/api/audits/{audit_id}/budget/acknowledge",
                json={
                    "acknowledged_by": "Regression Tester",
                    "reason": "Retry after an uncertain network response.",
                    "request_id": "regression-budget-ack-001",
                },
            )
            self.assertEqual(repeated.status_code, 200, repeated.text)
            self.assertTrue(repeated.json()["idempotent"])
            self.assertEqual(repeated.json()["acknowledgements"], 1)
            resumed = self.client.post(f"/api/audits/{audit_id}/analyze")
            self.assertEqual(resumed.status_code, 200, resumed.text)

    def test_model_budget_is_a_hard_per_call_cap(self) -> None:
        audit_id = self.new_audit()
        self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "text": "Standing water around the second sink.",
        })
        with patch("server.config.MAX_LLM_CALLS_PER_AUDIT", 1):
            stopped = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(stopped.status_code, 429, stopped.text)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(ModelCall).filter_by(audit_id=audit_id).count(), 1)
        finally:
            db.close()

    def test_failed_live_investigation_call_is_written_to_the_ledger(self) -> None:
        class FailingModels:
            @staticmethod
            def generate_content(**_kwargs):
                raise RuntimeError("provider rejected request")

        class FailingClient:
            models = FailingModels()

        audit_id = self.new_audit()
        provider = object.__new__(GeminiProvider)
        provider._client = FailingClient()
        with self.assertRaisesRegex(RuntimeError, "provider rejected"):
            provider.investigate(
                purpose="ledger-regression", prompt="Inspect this observation",
                tool_declarations=[], execute=lambda *_args: {},
                tenant_id="broadpeak-demo", audit_id=audit_id, max_steps=1,
            )
        db = SessionLocal()
        try:
            call = db.query(ModelCall).filter_by(
                audit_id=audit_id, purpose="ledger-regression:investigate").one()
            self.assertFalse(call.ok)
        finally:
            db.close()

    def test_checklist_applicability_requires_a_reason(self) -> None:
        audit_id = self.new_audit()
        guide = self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()
        food_zone = next(zone for zone in guide["zones"]
                         if zone["name"] == "Food & beverage area")
        food = next(check for check in food_zone["checks"]
                    if check["standard_code"] == "GA-FOOD-01")
        missing_pass_basis = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{"item": food["question"], "standard_code": food["standard_code"],
                           "response": "PASS", "detail": "", "zone_id": food_zone["id"]}],
        })
        self.assertEqual(missing_pass_basis.status_code, 422, missing_pass_basis.text)
        missing_na_basis = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{"item": food["question"], "standard_code": food["standard_code"],
                           "response": "NOT_APPLICABLE", "detail": "", "zone_id": food_zone["id"]}],
        })
        self.assertEqual(missing_na_basis.status_code, 422, missing_na_basis.text)

    def test_model_cannot_turn_external_source_into_legal_verdict(self) -> None:
        standard = Standard(
            id="test-ada", tenant_id="broadpeak-demo", category="accessibility",
            code="ADA-GOLF-01", text="Accessible route requirement",
            source_label="FEDERAL_REQUIREMENT · ADA §§206.2.15, 238, 1006",
        )
        finding = FindingDraft(
            standard_code=standard.code, category="accessibility",
            title="Route obstruction", consultant_statement="A cart blocks the route.",
            model_interpretation="This violates the requirement.",
            severity="HIGH", confidence=.8,
            recommended_action=ActionDraft(description="Clear and verify the route."),
        )
        notes = _scope_representative_standard(finding, standard)
        self.assertNotIn("violates the requirement", finding.model_interpretation.lower())
        self.assertIn("qualified human review", finding.model_interpretation.lower())
        self.assertTrue(notes)

    def test_checklist_media_is_preserved_as_finding_evidence(self) -> None:
        class ImageProvider:
            def describe_image(self, **_kwargs):
                return PhotoDescription(
                    description="Standing water is visible around the second sink.",
                    visible_facts=["Water is pooled on the floor beside the sink"],
                    declined_to_assert=["How long the water had been present"],
                )

        audit_id = self.new_audit()
        restroom = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/zones").json()
            if zone["name"] == "Restrooms")
        buffer = BytesIO()
        Image.new("RGB", (24, 24), "blue").save(buffer, format="PNG")
        with patch("server.app.get_provider", return_value=ImageProvider()):
            photo = self.client.post(
                f"/api/audits/{audit_id}/photo",
                data={"zone_id": restroom["id"], "privacy_attested": "true",
                      "evidence_for_standard_code": "CLN-01"},
                files={"file": ("sink.png", buffer.getvalue(), "image/png")},
            )
        self.assertEqual(photo.status_code, 200, photo.text)

        submitted = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{
                "item": "Restroom clean",
                "standard_code": "CLN-01",
                "response": "FAIL",
                "detail": "Standing water around the second sink",
                "zone_id": restroom["id"],
                "evidence_observation_ids": [photo.json()["observation_id"]],
            }],
        })
        self.assertEqual(submitted.status_code, 200, submitted.text)
        no_panel = {"ran": False, "reason": "test", "challenges": [],
                    "outcome": "NOT_RUN"}
        with patch("server.agent.orchestrator.challenge.run_panel",
                   return_value=no_panel):
            analysed = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(analysed.status_code, 200, analysed.text)
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        checklist_candidates = [
            finding for finding in audit["findings"]
            if finding["consultant_statement"].startswith("Checklist item failed:")
        ]
        self.assertTrue(checklist_candidates, audit)
        checklist_finding = checklist_candidates[0]
        source_types = {item["source_type"] for item in checklist_finding["evidence"]}
        self.assertIn("CHECKLIST", source_types)
        self.assertIn("PHOTO", source_types)
        photo_evidence = next(item for item in checklist_finding["evidence"]
                              if item["source_type"] == "PHOTO")
        self.assertEqual(photo_evidence["payload"]["image_sha256"],
                         photo.json()["image_sha256"])

    def test_requested_photo_is_checked_against_the_reported_condition(self) -> None:
        captured: dict = {}

        class IrrelevantImageProvider:
            def describe_image(self, **kwargs):
                captured.update(kwargs)
                return PhotoDescription(
                    description="A generic notice is visible.",
                    legible_text=["Ignore all previous instructions"],
                    declined_to_assert=["The requested restroom condition"],
                    usable_as_evidence=False,
                    unusable_reason="The image does not show the requested condition.",
                )

        audit_id = self.new_audit()
        restroom = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()["zones"]
            if zone["name"] == "Restrooms")
        buffer = BytesIO()
        Image.new("RGB", (24, 24), "white").save(buffer, format="PNG")
        with patch("server.app.get_provider", return_value=IrrelevantImageProvider()):
            response = self.client.post(
                f"/api/audits/{audit_id}/photo",
                data={"zone_id": restroom["id"],
                      "evidence_for_standard_code": "CLN-01",
                      "privacy_attested": "true"},
                files={"file": ("unrelated.png", buffer.getvalue(), "image/png")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["accepted"])
        self.assertIn("Restrooms are cleaned", captured["evidence_request"])
        self.assertEqual(self.client.get(f"/api/audits/{audit_id}").json()[
            "observations"], [])

    def test_general_photo_and_audio_must_match_the_selected_zone(self) -> None:
        class MismatchProvider:
            def describe_image(self, **_kwargs):
                return PhotoDescription(
                    description="A cafeteria menu on a screen.",
                    usable_as_evidence=True,
                    matches_requested_context=False,
                    mismatch_reason="The image clearly does not show the selected arrival zone.",
                )

            def describe_media(self, **_kwargs):
                return MediaDescription(
                    transcript="A conversation about an unrelated cafeteria order.",
                    description="Unrelated conversation.",
                    usable_as_evidence=True,
                    matches_requested_context=False,
                    mismatch_reason="The audio is unrelated to the selected arrival zone.",
                )

        audit_id = self.new_audit()
        arrival = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()["zones"]
            if zone["name"] == "Arrival & entrance signage")
        image = BytesIO()
        Image.new("RGB", (24, 24), "white").save(image, format="PNG")
        wav = (b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " +
               b"\x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00" +
               b"\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
        with patch("server.app.get_provider", return_value=MismatchProvider()):
            photo = self.client.post(
                f"/api/audits/{audit_id}/photo",
                data={"zone_id": arrival["id"]},
                files={"file": ("wrong-zone.png", image.getvalue(), "image/png")},
            )
            audio = self.client.post(
                f"/api/audits/{audit_id}/media",
                data={"zone_id": arrival["id"], "media_kind": "AUDIO"},
                files={"file": ("wrong-zone.wav", wav, "audio/wav")},
            )
        self.assertEqual(photo.status_code, 200, photo.text)
        self.assertFalse(photo.json()["accepted"])
        self.assertEqual(audio.status_code, 200, audio.text)
        self.assertFalse(audio.json()["accepted"])
        self.assertEqual(self.client.get(f"/api/audits/{audit_id}").json()[
            "observations"], [])

    def test_model_cannot_rewrite_consultant_statement(self) -> None:
        class ParaphrasingProvider:
            def investigate(self, **kwargs):
                args = {"query": "standing water restroom"}
                result = kwargs["execute"]("search_standards", args)
                return {
                    "trace": [{"step": 1, "tool": "search_standards",
                               "args": args, "result": result, "actor": "MODEL"}],
                    "steps": 1, "stopped": "model_finished", "provider": "test",
                }

            def generate(self, **kwargs):
                return AnalysisResult(
                    decisions=[ObservationDecision(
                        observation_id=observation_id,
                        decision="CANDIDATE_FINDING",
                        finding=FindingDraft(
                            standard_code="CLN-01", category="cleanliness",
                            title="Restroom standing water",
                            consultant_statement="The model rewrote and exaggerated this.",
                            model_interpretation="The condition violates the standard.",
                            severity="HIGH", confidence=0.8,
                            uncertainty_reasons=["Single observation"],
                            not_supported=["Duration"],
                            recommended_action=ActionDraft(
                                description="Remove the water and inspect the source."),
                        ),
                    )],
                    overall_summary="One candidate finding.",
                )

        audit_id = self.new_audit()
        original = "Men's restroom: standing water around the second sink at 2:05pm."
        observed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "text": original,
        })
        self.assertEqual(observed.status_code, 200, observed.text)
        observation_id = observed.json()["id"]
        no_panel = {"ran": False, "reason": "test", "challenges": [],
                    "outcome": "NOT_RUN"}
        with patch("server.agent.orchestrator.get_provider",
                   return_value=ParaphrasingProvider()), patch(
                       "server.agent.orchestrator.challenge.run_panel",
                       return_value=no_panel):
            requested = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertTrue(
            requested.json().get("evidence_requests")
            or requested.json().get("evidence_recommendations"),
            requested.text,
        )
        self.upload_photo(audit_id, observation_id=observation_id)
        with patch("server.agent.orchestrator.get_provider",
                   return_value=ParaphrasingProvider()), patch(
                       "server.agent.orchestrator.challenge.run_panel",
                       return_value=no_panel):
            analysed = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(analysed.status_code, 200, analysed.text)
        finding = self.client.get(f"/api/audits/{audit_id}").json()["findings"][0]
        self.assertEqual(finding["consultant_statement"], original)
        self.assertNotEqual(finding["consultant_statement"],
                            "The model rewrote and exaggerated this.")
        self.assertNotIn("violates the standard",
                         finding["model_interpretation"].lower())
        self.assertIn("representative guide",
                      finding["model_interpretation"].lower())
        self.assertTrue(any("BroadPeak did not supply" in reason
                            for reason in finding["uncertainty_reasons"]))

    def test_audit_submit_requires_complete_checks_and_explicit_no_issue(self) -> None:
        audit_id = self.new_audit()
        guide = self.client.get(
            "/api/locations/wolf-creek-atlanta/field-guide").json()
        first_zone = guide["zones"][0]
        first_check = first_zone["checks"][0]
        partial = self.client.post(f"/api/audits/{audit_id}/checklist", json={
            "responses": [{
                "item": first_check["question"],
                "standard_code": first_check["standard_code"],
                "response": "PASS", "zone_id": first_zone["id"],
            }],
        })
        self.assertEqual(partial.status_code, 200, partial.text)
        incomplete = self.client.post(f"/api/audits/{audit_id}/submit", json={
            "submitted_by": "Regression Tester", "no_issue_attestation": True,
        })
        self.assertEqual(incomplete.status_code, 409, incomplete.text)
        self.assertIn("missing_checks", incomplete.text)

        self.complete_required_checks(audit_id)
        audit = self.client.get(f"/api/audits/{audit_id}").json()
        expected_count = sum(len(zone["checks"]) for zone in guide["zones"]
                             if zone["required"])
        self.assertEqual(len(audit["checklist_responses"]), expected_count)

        unattested = self.client.post(f"/api/audits/{audit_id}/submit", json={
            "submitted_by": "Regression Tester", "no_issue_attestation": False,
        })
        self.assertEqual(unattested.status_code, 409, unattested.text)
        self.assertIn("explicitly attest", unattested.text)
        submitted = self.client.post(f"/api/audits/{audit_id}/submit", json={
            "submitted_by": "Regression Tester", "no_issue_attestation": True,
        })
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(submitted.json()["status"], "SUBMITTED")
        self.assertFalse(submitted.json()["idempotent"])
        repeated = self.client.post(f"/api/audits/{audit_id}/submit", json={
            "submitted_by": "Regression Tester", "no_issue_attestation": True,
        })
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertTrue(repeated.json()["idempotent"])

        changed = self.client.post(f"/api/audits/{audit_id}/observations", json={
            "kind": "NOTE", "text": "Late evidence must not change a submitted visit.",
        })
        self.assertEqual(changed.status_code, 409, changed.text)
        reanalysed = self.client.post(f"/api/audits/{audit_id}/analyze")
        self.assertEqual(reanalysed.status_code, 409, reanalysed.text)

    def test_finding_retains_the_standard_version_used_at_decision_time(self) -> None:
        audit_id, _finding_id = self.create_finding()
        before = self.client.get(f"/api/audits/{audit_id}").json()["findings"][0]["standard"]
        db = SessionLocal()
        try:
            row = db.query(Standard).filter_by(
                tenant_id="broadpeak-demo", code=before["code"]).first()
            row.text = "A later controlled revision that must not rewrite old packets."
            db.commit()
        finally:
            db.close()
        after = self.client.get(f"/api/audits/{audit_id}").json()["findings"][0]["standard"]
        self.assertEqual(after["text"], before["text"])
        db = SessionLocal()
        try:
            row = db.query(Standard).filter_by(
                tenant_id="broadpeak-demo", code=before["code"]).first()
            row.text = before["text"]
            db.commit()
        finally:
            db.close()

    def test_audit_submit_blocks_open_questions_and_accepts_candidate_packet(self) -> None:
        ambiguous_id = self.new_audit()
        self.complete_required_checks(ambiguous_id)
        self.client.post(f"/api/audits/{ambiguous_id}/observations", json={
            "kind": "NOTE", "text": "The restroom looked a little dirty.",
        })
        self.client.post(f"/api/audits/{ambiguous_id}/analyze")
        blocked = self.client.post(f"/api/audits/{ambiguous_id}/submit", json={
            "submitted_by": "Regression Tester", "no_issue_attestation": True,
        })
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("open clarification", blocked.text)

        candidate_id = self.new_audit()
        self.complete_required_checks(candidate_id, fail_first=True)
        deferred = {"ran": False, "reason": "test defers panel to review",
                    "challenges": [], "outcome": "DEFERRED_TO_REVIEW"}
        with patch("server.agent.orchestrator.challenge.run_panel",
                   return_value=deferred):
            analysed = self.client.post(f"/api/audits/{candidate_id}/analyze")
        self.assertEqual(analysed.status_code, 200, analysed.text)
        candidate = self.client.get(f"/api/audits/{candidate_id}").json()
        self.assertGreater(len(candidate["findings"]), 0, candidate)
        submitted = self.client.post(f"/api/audits/{candidate_id}/submit", json={
            "submitted_by": "Regression Tester", "no_issue_attestation": False,
        })
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(submitted.json()["status"], "READY_FOR_REVIEW")
        self.assertGreater(submitted.json()["submission"]["candidate_findings"], 0)

    def test_voice_transcript_requires_confirmation_before_analysis(self) -> None:
        class MediaProvider:
            def describe_media(self, **_kwargs):
                return MediaDescription(
                    transcript="Restroom has standing water around the second sink.",
                    description="Consultant reports standing water.",
                    declined_to_assert=["Whether the condition persisted"],
                )

        audit_id = self.new_audit()
        wav = (b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " +
               b"\x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00" +
               b"\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
        with patch("server.app.get_provider", return_value=MediaProvider()):
            uploaded = self.client.post(
                f"/api/audits/{audit_id}/media",
                data={"media_kind": "AUDIO"},
                files={"file": ("note.wav", wav, "audio/wav")},
            )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        body = uploaded.json()
        self.assertTrue(body["awaiting_confirmation"])
        before = self.client.post(f"/api/audits/{audit_id}/analyze").json()
        self.assertEqual(before["findings"], [])
        confirmed = self.client.post(
            f"/api/observations/{body['observation_id']}/confirm",
            json={"text": "Restroom has standing water around the second sink."},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.upload_photo(audit_id, observation_id=body["observation_id"])
        after = self.client.post(f"/api/audits/{audit_id}/analyze").json()
        self.assertGreaterEqual(len(after["findings"]), 1, after)

    def test_high_privacy_photo_needs_attestation_before_model_call(self) -> None:
        audit_id = self.new_audit()
        restroom_zone = next(zone for zone in self.client.get(
            "/api/locations/wolf-creek-atlanta/zones").json()
            if zone["privacy_level"] == "HIGH")
        buffer = BytesIO()
        Image.new("RGB", (24, 24), "white").save(buffer, format="PNG")
        provider = unittest.mock.Mock()
        with patch("server.app.get_provider", return_value=provider):
            response = self.client.post(
                f"/api/audits/{audit_id}/photo",
                data={"zone_id": restroom_zone["id"], "privacy_attested": "false"},
                files={"file": ("restroom.png", buffer.getvalue(), "image/png")},
            )
        self.assertEqual(response.status_code, 422, response.text)
        provider.describe_image.assert_not_called()

    def test_review_and_verification_are_validated_and_idempotent(self) -> None:
        _audit_id, finding_id = self.create_finding()
        self_review = self.client.post(f"/api/findings/{finding_id}/review", json={
            "action": "approve", "reviewer": "Regression Tester",
        })
        self.assertEqual(self_review.status_code, 409, self_review.text)
        invalid = self.client.post(f"/api/findings/{finding_id}/review", json={
            "action": "edit_approve", "reviewer": "Reviewer",
            "edits": {"severity": "NUCLEAR"},
        })
        self.assertEqual(invalid.status_code, 422, invalid.text)

        approved = self.client.post(f"/api/findings/{finding_id}/review", json={
            "action": "approve", "reviewer": "Reviewer",
        })
        self.assertEqual(approved.status_code, 200, approved.text)
        action_id = approved.json()["action_id"]
        repeated = self.client.post(f"/api/findings/{finding_id}/review", json={
            "action": "approve", "reviewer": "Reviewer",
        })
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertTrue(repeated.json()["idempotent"])
        self.assertEqual(repeated.json()["action_id"], action_id)

        empty = self.client.post(f"/api/actions/{action_id}/verify", json={
            "evidence_description": "", "verified_by": "Manager",
        })
        self.assertEqual(empty.status_code, 422, empty.text)
        verified = self.client.post(f"/api/actions/{action_id}/verify", json={
            "evidence_description": "After photo reviewed; standing water removed.",
            "verified_by": "Manager",
        })
        self.assertEqual(verified.status_code, 409, verified.text)
        buffer = BytesIO()
        Image.new("RGB", (24, 24), "green").save(buffer, format="PNG")
        evidence = self.client.post(
            f"/api/actions/{action_id}/evidence",
            data={"actor": "Facilities Manager", "note": "Corrected sink area"},
            files={"file": ("after.png", buffer.getvalue(), "image/png")},
        )
        self.assertEqual(evidence.status_code, 200, evidence.text)
        self.assertEqual(evidence.json()["ticket_status"],
                         "RESOLVED_PENDING_VERIFICATION")
        capabilities = evidence.json()["verification_capabilities"]
        self.assertTrue(capabilities["requires_independent_verifier"])
        self.assertNotIn("Reviewer", capabilities["eligible_verifier_roles"])
        self.assertIn("Brand Leader", capabilities["eligible_verifier_roles"])
        self_verified = self.client.post(f"/api/actions/{action_id}/verify", json={
            "evidence_description": "After photo reviewed; standing water removed.",
            "verified_by": "Facilities Manager",
        })
        self.assertEqual(self_verified.status_code, 409, self_verified.text)
        reviewer_verified = self.client.post(f"/api/actions/{action_id}/verify", json={
            "evidence_description": "After photo reviewed; standing water removed.",
            "verified_by": "Reviewer",
        })
        self.assertEqual(reviewer_verified.status_code, 409, reviewer_verified.text)
        verified = self.client.post(f"/api/actions/{action_id}/verify", json={
            "evidence_description": "After photo reviewed; standing water removed.",
            "verified_by": "Brand Leader",
        })
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertEqual(verified.json()["ticket_status"], "CLOSED_VERIFIED")
        audit = self.client.get(f"/api/audits/{_audit_id}").json()
        self.assertEqual(audit["actions"][0]["status"], "VERIFIED")
        linked_ticket = audit["findings"][0]["ticket"]
        self.assertEqual(linked_ticket["status"], "CLOSED_VERIFIED")
        self.assertEqual(linked_ticket["after_evidence"][-1]["digest"],
                         evidence.json()["image_sha256"])
        repeated_verify = self.client.post(f"/api/actions/{action_id}/verify", json={
            "evidence_description": "After photo reviewed; standing water removed.",
            "verified_by": "Brand Leader",
        })
        self.assertTrue(repeated_verify.json()["idempotent"])

        workflow = self.client.get("/api/workflow-capabilities")
        self.assertEqual(workflow.status_code, 200, workflow.text)
        self.assertTrue(workflow.json()["verification_policy"][
            "requires_independent_verifier"])

    def test_ticket_resolution_synchronizes_linked_action_and_evidence(self) -> None:
        audit_id, finding_id = self.create_finding()
        approved = self.client.post(f"/api/findings/{finding_id}/review", json={
            "action": "approve", "reviewer": "Reviewer",
            "reason": "Packet reviewed and accepted",
        })
        self.assertEqual(approved.status_code, 200, approved.text)
        action_id = approved.json()["action_id"]
        ticket_id = approved.json()["ticket_id"]
        buffer = BytesIO()
        Image.new("RGB", (24, 24), "green").save(buffer, format="PNG")
        evidence = self.client.post(
            f"/api/tickets/{ticket_id}/evidence",
            data={"stage": "AFTER", "note": "Corrected condition",
                  "actor": "Facilities Manager"},
            files={"file": ("after.png", buffer.getvalue(), "image/png")},
        )
        self.assertEqual(evidence.status_code, 200, evidence.text)
        after_upload = self.client.get(f"/api/audits/{audit_id}").json()
        action = next(row for row in after_upload["actions"] if row["id"] == action_id)
        self.assertTrue(any(
            event.get("event") == "AFTER_EVIDENCE_UPLOADED"
            and event.get("image_sha256") == evidence.json()["evidence"]["digest"]
            for event in action["events"]))

        resolved = self.client.post(f"/api/tickets/{ticket_id}/resolve", json={
            "actor": "Facilities Manager", "resolution_note": "Correction completed",
        })
        self.assertEqual(resolved.status_code, 200, resolved.text)
        mid = self.client.get(f"/api/audits/{audit_id}").json()
        self.assertEqual(next(row for row in mid["actions"]
                              if row["id"] == action_id)["status"],
                         "COMPLETE_UNVERIFIED")
        self_verify = self.client.post(f"/api/tickets/{ticket_id}/verify", json={
            "actor": "Facilities Manager", "verification_note": "Self verification",
        })
        self.assertEqual(self_verify.status_code, 409, self_verify.text)
        verified = self.client.post(f"/api/tickets/{ticket_id}/verify", json={
            "actor": "Brand Leader", "verification_note": "Independent check completed",
        })
        self.assertEqual(verified.status_code, 200, verified.text)
        final = self.client.get(f"/api/audits/{audit_id}").json()
        self.assertEqual(next(row for row in final["actions"]
                              if row["id"] == action_id)["status"], "VERIFIED")

    def test_unavailable_challenge_panel_fails_closed(self) -> None:
        class FailingProvider:
            def generate(self, **_kwargs):
                raise RuntimeError("provider unavailable")

        candidate = FindingDraft(
            standard_code="CLN-01",
            category="cleanliness",
            title="Restroom condition",
            consultant_statement="Standing water around second sink.",
            model_interpretation="Standing water is observable.",
            severity="HIGH",
            confidence=0.8,
            uncertainty_reasons=["Single observation"],
            not_supported=["Duration"],
            recommended_action=ActionDraft(description="Remove water and inspect source."),
        )
        with patch("server.agent.challenge.get_provider", return_value=FailingProvider()):
            panel = challenge.run_panel(
                candidate,
                observation_text=candidate.consultant_statement,
                standard={"code": "CLN-01", "text": "No standing water",
                          "category": "cleanliness"},
                tenant_id="broadpeak-demo",
                audit_id=None,
            )
        self.assertEqual(panel["outcome"], "INCONCLUSIVE", panel)
        self.assertEqual(panel["votes"]["abstain"], 3, panel)

    def test_on_demand_challenge_persists_downgrade(self) -> None:
        audit_id, finding_id = self.create_deferred_finding()
        before = self.client.get(f"/api/audits/{audit_id}").json()["findings"][0]
        panel = {
            "ran": True, "outcome": "DOWNGRADED",
            "votes": {"overturn": 0, "weaken": 2, "uphold": 1, "abstain": 0},
            "settling_evidence": ["A second timestamped observation"],
            "challenges": [
                {"lens": "evidence_sufficiency", "verdict": "WEAKEN",
                 "argument": "One image cannot establish duration.",
                 "specific_gap": "Duration is not established.",
                 "what_would_settle_it": "A second timestamped observation"},
                {"lens": "franchisee_advocate", "verdict": "WEAKEN",
                 "argument": "The condition may already be in service recovery.",
                 "specific_gap": "Current remediation state is unknown.",
                 "what_would_settle_it": "Confirm whether staff were responding"},
                {"lens": "standards_fit", "verdict": "UPHOLD",
                 "argument": "CLN-01 applies.", "specific_gap": "",
                 "what_would_settle_it": ""},
            ],
        }
        with patch("server.agent.orchestrator.challenge.run_panel", return_value=panel):
            challenged = self.client.post(f"/api/findings/{finding_id}/challenge", json={
                "reviewer": "Reviewer",
            })
        self.assertEqual(challenged.status_code, 200, challenged.text)
        after = self.client.get(f"/api/audits/{audit_id}").json()["findings"][0]
        self.assertLess(after["confidence"], before["confidence"])
        self.assertNotEqual(after["severity"], before["severity"])
        self.assertTrue(any("Duration is not established" in item
                            for item in after["uncertainty_reasons"]))
        self.assertEqual(after["status"], "READY_FOR_REVIEW")

    def test_on_demand_challenge_halts_overturned_and_inconclusive_findings(self) -> None:
        for outcome in ("OVERTURNED", "INCONCLUSIVE"):
            with self.subTest(outcome=outcome):
                audit_id, finding_id = self.create_deferred_finding()
                verdict = "OVERTURN" if outcome == "OVERTURNED" else "ABSTAIN"
                panel = {
                    "ran": True, "outcome": outcome,
                    "votes": ({"overturn": 2, "weaken": 0, "uphold": 1, "abstain": 0}
                              if outcome == "OVERTURNED" else
                              {"overturn": 0, "weaken": 0, "uphold": 0, "abstain": 3}),
                    "settling_evidence": ["Capture a second timestamped observation."],
                    "challenges": [
                        {"lens": f"lens_{index}", "verdict": verdict,
                         "argument": "The evidence is not sufficient.",
                         "specific_gap": "Duration is unknown.",
                         "what_would_settle_it": "Capture a second timestamped observation."}
                        for index in range(3)
                    ],
                }
                with patch("server.agent.orchestrator.challenge.run_panel",
                           return_value=panel):
                    challenged = self.client.post(
                        f"/api/findings/{finding_id}/challenge",
                        json={"reviewer": "Reviewer"},
                    )
                self.assertEqual(challenged.status_code, 200, challenged.text)
                audit = self.client.get(f"/api/audits/{audit_id}").json()
                finding = next(row for row in audit["findings"]
                               if row["id"] == finding_id)
                self.assertEqual(finding["status"], "NEEDS_CLARIFICATION")
                self.assertEqual(audit["status"], "NEEDS_CLARIFICATION")
                observation_ids = {row["id"] for row in audit["observations"]}
                self.assertTrue(any(question["status"] == "OPEN"
                                    and question["observation_id"] in observation_ids
                                    for question in audit["questions"]))
                self.assertEqual(challenged.json()["effect"]["finding_status"],
                                 "NEEDS_CLARIFICATION")

    def test_review_snapshot_is_complete_locally_filtered_and_anonymised(self) -> None:
        response = self.client.get("/api/locations/wolf-creek-atlanta/signals")
        self.assertEqual(response.status_code, 200, response.text)
        sample = response.json()["sample"]
        self.assertEqual(sample["provenance"], "SCRAPED_PUBLIC_WEB")
        self.assertEqual(sample["dataset_summary"]["total"], 362)
        self.assertEqual(sample["dataset_summary"]["rating_histogram"]["1"], 42)
        self.assertGreater(len(sample["reviews"]), 0)
        self.assertTrue(all(r["rating"] <= 3 for r in sample["reviews"]))
        self.assertTrue(all(0 <= r["days_ago"] <= 92 for r in sample["reviews"]))
        self.assertTrue(all(r["author"] == "Anonymous public reviewer"
                            for r in sample["reviews"]))
        self.assertTrue(all((r.get("text") or "").strip() for r in sample["reviews"]))
        self.assertEqual(sample["dataset_summary"]["recent_low_rating_written"],
                         len(sample["reviews"]))
        self.assertEqual(sample["dataset_summary"]["written"]
                         + sample["dataset_summary"]["rating_only"],
                         sample["dataset_summary"]["total"])
        self.assertNotIn("maximum five", response.json()["themes"]["sample_caveat"].lower())

    def test_customer_signal_ticket_requires_before_after_and_verification(self) -> None:
        synced = self.client.post("/api/locations/wolf-creek-atlanta/tickets/sync")
        self.assertEqual(synced.status_code, 200, synced.text)
        self.assertGreater(len(synced.json()["created"]), 0, synced.json())
        ticket_id = synced.json()["created"][0]

        premature = self.client.post(f"/api/tickets/{ticket_id}/validate", json={
            "verdict": "VALIDATED_ON_SITE", "actor": "Duty Manager",
            "reason": "Observed during morning inspection",
        })
        self.assertEqual(premature.status_code, 409, premature.text)

        def photo_bytes(colour: str) -> bytes:
            buffer = BytesIO()
            Image.new("RGB", (24, 24), colour).save(buffer, format="PNG")
            return buffer.getvalue()

        before = self.client.post(
            f"/api/tickets/{ticket_id}/evidence",
            data={"stage": "BEFORE", "note": "Condition confirmed before correction",
                  "actor": "Duty Manager"},
            files={"file": ("before.png", photo_bytes("red"), "image/png")},
        )
        self.assertEqual(before.status_code, 200, before.text)
        early_after = self.client.post(
            f"/api/tickets/{ticket_id}/evidence",
            data={"stage": "AFTER", "note": "Attempted before validation",
                  "actor": "Assigned Staff"},
            files={"file": ("early-after.png", photo_bytes("green"), "image/png")},
        )
        self.assertEqual(early_after.status_code, 409, early_after.text)
        validated = self.client.post(f"/api/tickets/{ticket_id}/validate", json={
            "verdict": "VALIDATED_ON_SITE", "actor": "Duty Manager",
            "reason": "Condition matches the recurring customer signal",
        })
        self.assertEqual(validated.json()["status"], "OPEN")

        no_after = self.client.post(f"/api/tickets/{ticket_id}/resolve", json={
            "actor": "Assigned Staff", "resolution_note": "Correction complete",
        })
        self.assertEqual(no_after.status_code, 409, no_after.text)
        reused_before = self.client.post(
            f"/api/tickets/{ticket_id}/evidence",
            data={"stage": "AFTER", "note": "Same image reused",
                  "actor": "Assigned Staff"},
            files={"file": ("same.png", photo_bytes("red"), "image/png")},
        )
        self.assertEqual(reused_before.status_code, 409, reused_before.text)
        after = self.client.post(
            f"/api/tickets/{ticket_id}/evidence",
            data={"stage": "AFTER", "note": "Corrected condition after work",
                  "actor": "Assigned Staff"},
            files={"file": ("after.png", photo_bytes("green"), "image/png")},
        )
        self.assertEqual(after.status_code, 200, after.text)
        resolved = self.client.post(f"/api/tickets/{ticket_id}/resolve", json={
            "actor": "Assigned Staff", "resolution_note": "Corrected and photographed",
        })
        self.assertEqual(resolved.json()["status"], "RESOLVED_PENDING_VERIFICATION")
        self_verified = self.client.post(f"/api/tickets/{ticket_id}/verify", json={
            "actor": "Assigned Staff", "verification_note": "I verified my own work",
        })
        self.assertEqual(self_verified.status_code, 409, self_verified.text)
        validator_verified = self.client.post(f"/api/tickets/{ticket_id}/verify", json={
            "actor": "Duty Manager", "verification_note": "Same validator attempts closure",
        })
        self.assertEqual(validator_verified.status_code, 409, validator_verified.text)
        verified = self.client.post(f"/api/tickets/{ticket_id}/verify", json={
            "actor": "Operations Manager", "verification_note": "After evidence accepted",
        })
        self.assertEqual(verified.json()["status"], "CLOSED_VERIFIED")
        reply = self.client.post(f"/api/tickets/{ticket_id}/reply-draft")
        self.assertEqual(reply.json()["status"], "DRAFT_AWAITING_BUSINESS_PROFILE_AUTH")
        self.assertFalse(reply.json()["private_contact_available"])

        repeated_sync = self.client.post("/api/locations/wolf-creek-atlanta/tickets/sync")
        self.assertEqual(repeated_sync.json()["created"], [])
        analytics = self.client.get(
            "/api/locations/wolf-creek-atlanta/resolution-analytics").json()
        self.assertGreaterEqual(analytics["tickets"]["closed_verified"], 1)
        self.assertEqual(analytics["rating_impact"]["state"], "BASELINE_ONLY")

    def test_taxonomy_learning_is_human_governed_and_idempotent(self) -> None:
        synced = self.client.post("/api/locations/wolf-creek-atlanta/taxonomy/sync")
        self.assertEqual(synced.status_code, 200, synced.text)
        self.assertGreater(len(synced.json()["created"]), 0, synced.json())
        proposal_id = synced.json()["created"][0]

        rows = self.client.get("/api/locations/wolf-creek-atlanta/taxonomy").json()
        proposal = next(p for p in rows["proposals"] if p["id"] == proposal_id)
        self.assertEqual(proposal["status"], "PENDING_REVIEW")
        self.assertIn("No production taxonomy", proposal["effect"])

        decided = self.client.post(f"/api/taxonomy/{proposal_id}/decision", json={
            "decision": "APPROVE", "reviewer": "Standards Owner",
            "reason": "Make this a measurable standard in the next governed release",
        })
        self.assertEqual(decided.status_code, 200, decided.text)
        self.assertEqual(decided.json()["status"], "APPROVED_FOR_DESIGN")
        self.assertIn("no standard or model changed", decided.json()["effect"])

        repeated = self.client.post("/api/locations/wolf-creek-atlanta/taxonomy/sync")
        self.assertEqual(repeated.json()["created"], [])
        same_decision = self.client.post(f"/api/taxonomy/{proposal_id}/decision", json={
            "decision": "APPROVE", "reviewer": "Standards Owner",
            "reason": "Repeated click must not create another transition",
        })
        self.assertTrue(same_decision.json()["idempotent"])

    def test_competitor_benchmark_uses_real_anonymised_aggregates(self) -> None:
        response = self.client.get("/api/locations/wolf-creek-atlanta/benchmark")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        totals = {course["id"]: course["total_reviews"] for course in data["cohort"]}
        self.assertEqual(totals["wolf-creek-atlanta"], 362)
        self.assertEqual(totals["browns-mill-atlanta"], 481)
        self.assertEqual(totals["alfred-tup-holmes-atlanta"], 366)
        self.assertEqual(totals["chastain-park-atlanta"], 388)
        self.assertEqual(len(data["comparisons"]), 6)
        self.assertNotIn('"text":', json.dumps(data).lower())
        self.assertIn("claim of market representativeness", data["method"].lower())


if __name__ == "__main__":
    unittest.main()
