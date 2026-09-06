import uvicorn
import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(project_root, "backend")
sys.path.insert(0, backend_dir)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        timeout_keep_alive=300,
    )
