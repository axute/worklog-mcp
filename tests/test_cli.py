from worklog.auth import check_token
from worklog.cli import main
from worklog.db import Database


def test_token_lifecycle(tmp_path, capsys):
    path = str(tmp_path / "w.db")
    assert main(["--db", path, "token", "create", "laptop", "--scopes", "write"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert token.startswith("wl_")

    db = Database(path)
    assert check_token(db, token).client_id == "laptop"
    db.close()

    assert main(["--db", path, "token", "list"]) == 0
    out = capsys.readouterr().out
    assert "laptop" in out and "write" in out and "active" in out
    assert token not in out

    assert main(["--db", path, "token", "revoke", "laptop"]) == 0
    db = Database(path)
    assert check_token(db, token) is None
    db.close()

    assert main(["--db", path, "token", "revoke", "laptop"]) == 1
    assert "already revoked" in capsys.readouterr().err


def test_duplicate_and_unknown(tmp_path, capsys):
    path = str(tmp_path / "w.db")
    main(["--db", path, "token", "create", "x"])
    assert main(["--db", path, "token", "create", "x"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert main(["--db", path, "token", "revoke", "nope"]) == 1
