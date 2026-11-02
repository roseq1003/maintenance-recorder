"""SQLiteデータベースのバックアップ作成と古いバックアップの整理。

単独実行すると maintenance.db をSQLiteのbackup APIで複製する。
保存先と保持日数は環境変数で変更できる。
"""

from contextlib import closing
from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
from dotenv import load_dotenv
from typing import Optional, Union


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)
DB_PATH = BASE_DIR / "maintenance.db"
DEFAULT_BACKUP_DIR = BASE_DIR / "backups"
BACKUP_PREFIX = "maintenance_"
BACKUP_SUFFIX = ".db"
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


def backup_dir_from_env() -> Path:
    """BACKUP_DIRがあればその保存先、なければアプリ内backupsを使う。"""
    configured = os.environ.get("BACKUP_DIR", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_BACKUP_DIR


def retention_days_from_env() -> int:
    """BACKUP_RETENTION_DAYS未設定なら30日保持する。"""
    raw = os.environ.get("BACKUP_RETENTION_DAYS", "30").strip()
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
        return value
    except ValueError:
        raise RuntimeError("BACKUP_RETENTION_DAYSは1以上の整数で設定してください")


def prune_old_backups(
    backup_dir: Union[str, Path],
    current: datetime,
    retention_days: int,
) -> int:
    """このスクリプトの命名規則に合う、保持期間を過ぎたバックアップだけ削除する。"""
    if retention_days < 1:
        raise ValueError("retention_daysは1以上で指定してください")

    backup_dir = Path(backup_dir)
    cutoff = current - timedelta(days=retention_days)
    removed = 0

    for path in backup_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
        timestamp_text = path.name[len(BACKUP_PREFIX):-len(BACKUP_SUFFIX)]
        try:
            created_at = datetime.strptime(timestamp_text, TIMESTAMP_FORMAT)
        except ValueError:
            # 命名規則に合わないファイルは勝手に削除しない。
            continue

        if created_at < cutoff:
            path.unlink()
            removed += 1

    return removed


def create_backup(
    db_path: Union[str, Path] = DB_PATH,
    backup_dir: Optional[Union[str, Path]] = None,
    retention_days: int = 30,
    current: Optional[datetime] = None,
) -> Path:
    """SQLite backup APIで整合性のある複製を作り、古いバックアップを整理する。"""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"バックアップ元DBが見つかりません: {db_path}")
    if retention_days < 1:
        raise ValueError("retention_daysは1以上で指定してください")

    current = current or datetime.now()
    destination_dir = Path(backup_dir) if backup_dir is not None else DEFAULT_BACKUP_DIR
    destination_dir.mkdir(parents=True, exist_ok=True)

    backup_path = destination_dir / (
        f"{BACKUP_PREFIX}{current.strftime(TIMESTAMP_FORMAT)}{BACKUP_SUFFIX}"
    )

    if backup_path.exists():
        raise FileExistsError(f"同名のバックアップが既にあります: {backup_path}")

    # SQLite自身のbackup APIを使うため、Webアプリ稼働中でも一貫したDBを複製できる。
    with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(backup_path)) as destination:
        source.backup(destination)

    # 作成直後にSQLiteとして読めることを確認する。失敗時は壊れたバックアップを残さない。
    try:
        with closing(sqlite3.connect(backup_path)) as check:
            result = check.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise RuntimeError("バックアップDBの整合性確認に失敗しました")
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise

    prune_old_backups(destination_dir, current, retention_days)
    return backup_path


if __name__ == "__main__":
    try:
        path = create_backup(
            DB_PATH,
            backup_dir=backup_dir_from_env(),
            retention_days=retention_days_from_env(),
        )
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError, sqlite3.Error) as error:
        print(f"バックアップに失敗しました: {error}")
        raise SystemExit(1)

    print(f"バックアップ完了: {path}")
