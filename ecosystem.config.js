module.exports = {
  apps: [
    {
      name: "mugogo-api",
      script: "./venv/bin/uvicorn",
      args: "app.main:app --host 0.0.0.0 --port 8000 --workers 4",
      cwd: "/var/www/mugogoinc/mugogoinc_api",
      interpreter: "python3",
      env: {
        NODE_ENV: "production",
      }
    }
  ]
};
