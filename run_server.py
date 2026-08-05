import sys
sys.path.insert(0, '.')
import uvicorn
from backend.main import app
from backend.config import APP_HOST, APP_PORT

if __name__ == "__main__":
    # 不开 reload：Windows 下分离运行更稳。
    # 改 skill 脚本(web_render)已由 _load_web_render 的 importlib.reload 即时生效；
    # 只有改后端代码时才需要手动重启本服务。
    # 监听地址/端口从环境变量读（步骤 3.6），默认 127.0.0.1:8000。
    uvicorn.run(app, host=APP_HOST, port=APP_PORT)
