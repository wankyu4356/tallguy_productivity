import shutil
from fastapi import APIRouter
from app.config import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    env_errors = settings.validate_required()

    disk = shutil.disk_usage(str(settings.OUTPUT_DIR))
    disk_free_gb = round(disk.free / (1024**3), 2)

    checks = {
        "thebell_credentials": not any("THEBELL" in e for e in env_errors),
        "anthropic_api_key": not any("ANTHROPIC" in e for e in env_errors),
        "output_dir_exists": settings.OUTPUT_DIR.exists(),
        "disk_free_gb": disk_free_gb,
        "disk_ok": disk_free_gb > 0.5,
    }

    all_ok = all(v for k, v in checks.items() if k != "disk_free_gb")

    return {
        "status": "ok" if all_ok else "error",
        "checks": checks,
        "errors": env_errors if env_errors else None,
    }
