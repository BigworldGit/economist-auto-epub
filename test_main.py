import sys
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

# The production workflow installs requests. Pure unit tests do not need it.
sys.modules.setdefault("requests", MagicMock())
import main


class IssueCatchUpTests(unittest.TestCase):
    def setUp(self):
        self.folders = [
            {"issue_date": date(2026, 8, 15), "url": "issue-15"},
            {"issue_date": date(2026, 8, 22), "url": "issue-22"},
        ]

    def test_selects_previous_issue_when_it_was_published_after_last_run(self):
        publication_times = {
            date(2026, 8, 15): datetime(
                2026, 8, 15, 18, 2, tzinfo=ZoneInfo("Asia/Shanghai")
            )
        }

        selected = main.select_issue_folders_for_run(
            self.folders,
            target_issue_date=date(2026, 8, 22),
            publication_time_loader=publication_times.__getitem__,
        )

        self.assertEqual(
            [folder["issue_date"] for folder in selected],
            [date(2026, 8, 15), date(2026, 8, 22)],
        )

    def test_does_not_resend_previous_issue_when_it_was_available_last_run(self):
        publication_times = {
            date(2026, 8, 15): datetime(
                2026, 8, 14, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai")
            )
        }

        selected = main.select_issue_folders_for_run(
            self.folders,
            target_issue_date=date(2026, 8, 22),
            publication_time_loader=publication_times.__getitem__,
        )

        self.assertEqual(
            [folder["issue_date"] for folder in selected],
            [date(2026, 8, 22)],
        )

    def test_current_missing_still_catches_up_late_previous_issue(self):
        publication_times = {
            date(2026, 8, 15): datetime(
                2026, 8, 15, 18, 2, tzinfo=ZoneInfo("Asia/Shanghai")
            )
        }

        selected = main.select_issue_folders_for_run(
            self.folders[:1],
            target_issue_date=date(2026, 8, 22),
            publication_time_loader=publication_times.__getitem__,
        )

        self.assertEqual(
            [folder["issue_date"] for folder in selected],
            [date(2026, 8, 15)],
        )

    def test_target_date_accepts_dashes(self):
        self.assertEqual(
            main.determine_target_issue_date("2026-08-15"),
            date(2026, 8, 15),
        )

    @patch("main.download_file")
    @patch("main.convert_epub", side_effect=["converted-15", "converted-22"])
    @patch(
        "main.get_epub_from_folder",
        side_effect=[
            ("source-15", "Economist_2026.08.15.epub"),
            ("source-22", "Economist_2026.08.22.epub"),
        ],
    )
    @patch("main.get_issue_publication_time")
    @patch("main.send_mail")
    @patch("main.list_issue_folders")
    def test_main_sends_missed_and_current_in_one_email(
        self,
        list_issue_folders,
        send_mail,
        get_issue_publication_time,
        get_epub_from_folder,
        convert_epub,
        download_file,
    ):
        list_issue_folders.return_value = self.folders
        get_issue_publication_time.return_value = datetime(
            2026, 8, 15, 18, 2, tzinfo=ZoneInfo("Asia/Shanghai")
        )

        with patch.object(main, "ISSUE_DATE", "2026.08.22"):
            main.main()

        send_mail.assert_called_once_with(
            ["Economist_2026.08.15.epub", "Economist_2026.08.22.epub"],
            [date(2026, 8, 15), date(2026, 8, 22)],
        )
        self.assertEqual(get_epub_from_folder.call_count, 2)
        self.assertEqual(convert_epub.call_count, 2)
        self.assertEqual(download_file.call_count, 2)


if __name__ == "__main__":
    unittest.main()
