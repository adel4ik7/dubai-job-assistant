import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from db import Database, PROFILE_FIELDS, STATUSES
from services.product import PrivacyError, UserFiles, profile_gaps, validate_field


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        self.db = Database(self.root / "test.db")
        self.files = UserFiles(self.db, self.uploads)

    def cv(self, user, name="sample"):
        path = self.uploads / f"{user}_{name}.txt"
        path.write_text("Sample CV experience " * 10)
        return self.db.add_resume(user, path.name, str(path), path.read_text()), path

    def test_profile_create_read_update_delete_and_isolation(self):
        values = dict.fromkeys(PROFILE_FIELDS, "test")
        self.db.save_profile(1, **values)
        self.db.save_profile(2, full_name="Other")
        self.db.save_profile(1, desired_role="Analyst")
        profile = Database(self.db.path).get_profile(1)
        self.assertEqual(profile["desired_role"], "Analyst")
        self.assertEqual(profile["full_name"], "test")
        self.db.delete_profile(1)
        self.assertIsNone(self.db.get_profile(1))
        self.assertEqual(self.db.get_profile(2)["full_name"], "Other")
        with self.assertRaises(ValueError):
            self.db.save_profile(1, imaginary="bad")

    def test_validation(self):
        for field, value in (("years_experience", "-2"), ("years_experience", "ninety"),
                             ("date_applied", "2026-02-30"), ("date_applied", "2999-01-01"),
                             ("full_name", ""), ("status", "Whatever"), ("notes", "a" * 1001),
                             ("english_level", "Perfect")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_field(field, value)
        self.assertEqual(validate_field("years_experience", "2.5"), "2.5")
        self.assertEqual(validate_field("notes", "-"), "")

    def test_multiple_cvs_active_selection_and_persistence(self):
        first, _ = self.cv(1, "first")
        second, _ = self.cv(1, "second")
        other, _ = self.cv(2)
        self.assertEqual(self.db.active_resume(1)["id"], second)
        self.assertTrue(self.db.select_resume(1, first))
        self.assertEqual(Database(self.db.path).active_resume(1)["id"], first)
        self.assertEqual(self.db.latest_resume(1)["id"], second)
        self.assertFalse(self.db.select_resume(1, other))
        self.assertIsNone(self.db.get_resume(1, other))
        self.assertEqual(len(self.db.list_resumes(1)), 2)

    def test_delete_active_cv_selects_remaining(self):
        first, first_path = self.cv(1, "first")
        second, second_path = self.cv(1, "second")
        self.assertTrue(self.files.delete_cv(1, second))
        self.assertFalse(second_path.exists())
        self.assertEqual(self.db.active_resume(1)["id"], first)
        self.assertTrue(first_path.exists())
        self.assertTrue(self.files.delete_cv(1, first))
        self.assertIsNone(self.db.active_resume(1))

    def test_cv_delete_is_owner_scoped(self):
        resume_id, path = self.cv(2)
        self.assertFalse(self.files.delete_cv(1, resume_id))
        self.assertTrue(path.exists())

    def test_legacy_shared_file_survives_until_last_cv_deleted(self):
        first, path = self.cv(1)
        second = self.db.add_resume(1, "duplicate", str(path), "text")
        self.files.delete_cv(1, second)
        self.assertTrue(path.exists())
        self.files.delete_cv(1, first)
        self.assertFalse(path.exists())

    def test_application_fields_filters_and_literal_search(self):
        app_id = self.db.add_application(1, "Acme 100%", "Data Analyst", "note", status="HR screening",
            source="Referral", salary="AED 12000/month", date_applied="2026-01-01")
        self.db.add_application(1, "Elsewhere", "Developer", status="Saved")
        self.db.add_application(2, "Acme 100%", "Data Analyst")
        self.assertEqual(self.db.get_application(1, app_id)["source"], "Referral")
        self.assertIsNone(self.db.get_application(2, app_id))
        self.assertEqual(len(self.db.list_applications(1, status="HR screening", search="ANALYST")), 1)
        self.assertEqual(len(self.db.list_applications(1, search="%")), 1)
        self.assertEqual(self.db.list_applications(1, search="' OR 1=1 --"), [])
        self.assertEqual(len(self.db.list_applications(1, limit=1, offset=1)), 1)
        self.db.add_application(1, "КОМПАНИЯ", "Аналитик")
        self.assertEqual(len(self.db.list_applications(1, search="компания")), 1)

    def test_statuses_and_backwards_compatible_application_api(self):
        app_id = self.db.add_application(1, "Old Company", "Old Role", "Old note")
        self.assertEqual(self.db.get_application(1, app_id)["date_applied"], date.today().isoformat())
        for status in STATUSES:
            self.assertTrue(self.db.update_application_status(1, app_id, status))
        self.assertFalse(self.db.update_application_status(2, app_id, "Offer"))
        self.assertFalse(self.db.update_application_status(1, app_id, "Invented"))
        self.assertEqual(self.db.get_application(1, app_id)["notes"], "Old note")

    def test_dashboard_snapshot_definitions_and_empty_case(self):
        self.assertEqual(self.db.dashboard(1)["offer_rate"], 0)
        for status in STATUSES:
            self.db.add_application(1, "Acme", "Role", status=status)
        self.db.add_application(2, "Other", "Role", status="Offer")
        d = self.db.dashboard(1)
        self.assertEqual((d["total"], d["active"], d["interviews"], d["offers"], d["rejections"]), (9, 5, 3, 1, 1))
        self.assertEqual(d["submitted"], 7)
        self.assertEqual(d["offer_rate"], 14.3)
        self.assertEqual(d["interview_rate"], 42.9)

    def test_delete_all_removes_records_files_orphans_and_keeps_other_user(self):
        _, own = self.cv(1)
        _, other = self.cv(2)
        orphan = self.uploads / "1_failed.txt"
        orphan.write_text("failed upload")
        self.db.upsert_user(1, "user", "name")
        self.db.save_profile(1, full_name="Name")
        self.db.add_application(1, "Acme", "Role")
        self.db.reserve_ai_attempt(1, "2026-01-01", 2)
        self.files.delete_all(1)
        self.assertFalse(own.exists())
        self.assertFalse(orphan.exists())
        self.assertTrue(other.exists())
        self.assertIsNone(self.db.get_profile(1))
        self.assertEqual(self.db.list_resumes(1), [])
        self.assertEqual(self.db.list_applications(1), [])
        with closing(sqlite3.connect(self.db.path)) as conn:
            for table in ("users", "ai_usage", "active_resumes"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE telegram_id=1").fetchone()[0], 0)
        self.files.delete_all(1)  # idempotent retry

    def test_unsafe_path_preflight_does_not_delete_any_files(self):
        _, safe = self.cv(1)
        outside = self.root / "1_outside.txt"
        outside.write_text("must survive")
        self.db.add_resume(1, "outside", str(outside), "text")
        with self.assertRaises(PrivacyError):
            self.files.delete_all(1)
        self.assertTrue(outside.exists())
        self.assertTrue(safe.exists())
        self.assertEqual(len(self.db.list_resumes(1)), 2)

    def test_io_failure_retains_records_for_retry(self):
        resume_id, path = self.cv(1)
        with patch.object(Path, "unlink", side_effect=PermissionError), self.assertRaises(PrivacyError):
            self.files.delete_all(1)
        self.assertIsNotNone(self.db.get_resume(1, resume_id))
        self.files.delete_all(1)
        self.assertFalse(path.exists())

    def test_conflicting_file_ownership_fails_safely(self):
        _, path = self.cv(1)
        self.db.add_resume(2, "bad record", str(path), "text")
        with self.assertRaises(PrivacyError):
            self.files.delete_all(1)
        self.assertTrue(path.exists())

    def test_profile_gaps_do_not_invent_specialist_experience(self):
        report = profile_gaps({"years_experience": "8", "current_location": "Dubai", "english_level": "Beginner (A1)"},
                              "3 years Python experience. Fluent English. Own visa required.")
        self.assertIn("3+ years", report)
        self.assertIn("English", report)
        self.assertIn("Own / valid UAE visa", report)
        self.assertIn("does not change your CV score", report)


class MigrationTests(unittest.TestCase):
    def test_old_database_is_migrated_idempotently_without_data_loss(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.db"
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript("""CREATE TABLE users(telegram_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,created_at TEXT);
                    CREATE TABLE resumes(id INTEGER PRIMARY KEY AUTOINCREMENT,telegram_id INTEGER,filename TEXT,file_path TEXT,extracted_text TEXT,created_at TEXT);
                    CREATE TABLE applications(id INTEGER PRIMARY KEY AUTOINCREMENT,telegram_id INTEGER,company TEXT,role TEXT,status TEXT DEFAULT 'Applied',notes TEXT,created_at TEXT);
                    INSERT INTO users VALUES(1,'legacy','Legacy','2025-01-01');
                    INSERT INTO resumes VALUES(1,1,'old.txt','unused','old CV','2025-01-01');
                    INSERT INTO resumes VALUES(2,1,'new.txt','unused','new CV','2025-01-02');
                    INSERT INTO applications VALUES(1,1,'Company','Role','Custom old status','note','2025-01-01 10:00:00');""")
            db = Database(path)
            self.assertEqual(db.active_resume(1)["id"], 2)
            db.select_resume(1, 1)
            db = Database(path)
            self.assertEqual(db.active_resume(1)["id"], 1)
            app = db.get_application(1, 1)
            self.assertEqual((app["status"], app["notes"], app["date_applied"]), ("Custom old status", "note", "2025-01-01"))


if __name__ == "__main__":
    unittest.main()
