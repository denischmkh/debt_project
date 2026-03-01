from fastapi import FastAPI, Request, HTTPException
import subprocess
import os
import hmac
import hashlib

app = FastAPI()

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


@app.post("/deploy")
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
        # Используем Popen, чтобы не блокировать сервер на время деплоя
        subprocess.Popen(["bash", DEPLOY_SCRIPT])
        return {"status": "Deployment started in background"}
    except Exception as e:
        return {"status": "Error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn

    # Запускаем на порту 9000
    uvicorn.run(app, host="0.0.0.0", port=9000)