"""
DB 연결 관리: SSH 터널(선택) + SQLAlchemy 엔진.

사용법:
    with db_context() as engine:
        with engine.connect() as conn:
            conn.execute(...)

터널이 활성화된 경우 context 종료 시 터널도 함께 닫힌다.
"""

from contextlib import contextmanager
from sqlalchemy import create_engine, Engine
from sshtunnel import SSHTunnelForwarder

from app import config


def _dsn(host: str, port: int) -> str:
    return (
        f"mysql+pymysql://{config.RDS_USER}:{config.RDS_PASSWORD}"
        f"@{host}:{port}/{config.RDS_DB}"
        f"?charset=utf8mb4"
    )


@contextmanager
def db_context():
    """SSH 터널(옵션) + SQLAlchemy 엔진을 열고 닫는 context manager."""
    tunnel: SSHTunnelForwarder | None = None
    engine: Engine | None = None

    try:
        if config.TUNNEL_ENABLED:
            tunnel = SSHTunnelForwarder(
                (config.TUNNEL_SSH_HOST, config.TUNNEL_SSH_PORT),
                ssh_username=config.TUNNEL_SSH_USER,
                ssh_pkey=config.TUNNEL_SSH_KEY_PATH,
                remote_bind_address=(config.RDS_HOST, config.RDS_PORT),
                local_bind_address=("127.0.0.1", config.TUNNEL_LOCAL_PORT),
            )
            tunnel.start()
            dsn = _dsn("127.0.0.1", config.TUNNEL_LOCAL_PORT)
        else:
            dsn = _dsn(config.RDS_HOST, config.RDS_PORT)

        engine = create_engine(
            dsn,
            pool_pre_ping=True,       # 끊긴 커넥션 자동 감지
            pool_recycle=1800,        # 30분마다 커넥션 갱신
            echo=False,
        )
        yield engine

    finally:
        if engine:
            engine.dispose()
        if tunnel and tunnel.is_active:
            tunnel.stop()

