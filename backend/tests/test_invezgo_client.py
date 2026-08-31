"""Test unit buat invezgo_client.py — cuma _check_budget (fungsi murni, gak nembak API asli)."""
import datetime
import pytest
import invezgo_client as ic


@pytest.fixture(autouse=True)
def reset_usage():
    ic._usage["date"] = None
    ic._usage["count"] = 0
    yield
    ic._usage["date"] = None
    ic._usage["count"] = 0


def test_check_budget_increments_and_resets_per_day():
    ic._check_budget()
    ic._check_budget()
    assert ic._usage["count"] == 2


def test_check_budget_raises_after_limit():
    ic._usage["date"] = datetime.date.today().isoformat()
    ic._usage["count"] = ic.DAILY_BUDGET
    with pytest.raises(ic.InvezgoBudgetExceeded):
        ic._check_budget()


def test_check_budget_resets_on_new_date():
    ic._usage["date"] = "2000-01-01"  # tanggal lampau, beda dari hari ini
    ic._usage["count"] = ic.DAILY_BUDGET
    ic._check_budget()  # gak boleh raise, karena tanggalnya udah beda
    assert ic._usage["count"] == 1
