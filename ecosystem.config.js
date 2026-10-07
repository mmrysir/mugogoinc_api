module.exports = {
  apps: [
    {
      name: "mugogo-api",
      script: "/var/www/mugogoinc/mugogoinc_api/venv/bin/python",
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4",
      cwd: "/var/www/mugogoinc/mugogoinc_api",
      env: {
        NODE_ENV: "production",
      }
    }
  ]
};
