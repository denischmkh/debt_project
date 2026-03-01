from fastapi import APIRouter, Request, HTTPException
import subprocess
import os
import hmac
import hashlib

router = APIRouter(prefix="/deploy", tags=["deploy"])

# Секретный токен, который вы укажете в настройках GitHub
GH_SECRET = os.getenv("GH_SECRET")
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# Поднимаемся на уровень выше и находим deploy.sh
DEPLOY_SCRIPT = os.path.abspath(os.path.join(CURRENT_DIR, "..", "deploy.sh"))


def verify_signature(payload: bytes, signature: str):
    """Проверка, что запрос пришел именно от GitHub"""
    sha_name, signature_hash = signature.split('=')
    if sha_name != 'sha256':
        return False
    mac = hmac.new(GH_SECRET.encode(), payload, hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), signature_hash)


@router.post("/")
async def handle_deploy(request: Request):
    # 1. Проверяем подпись (безопасность)
    signature = request.headers.get("X-Hub-Signature-256")
    if not signature:
        raise HTTPException(status_code=403, detail="No signature")

    body = await request.body()
    if not verify_signature(body, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 2. Запускаем ваш Bash-скрипт
    try:
        # Используем run вместо Popen, чтобы увидеть результат в логах
        result = subprocess.run(
            ["bash", DEPLOY_SCRIPT],
            capture_output=True,
            text=True
        )
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")  # Вот здесь будет причина падения
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")