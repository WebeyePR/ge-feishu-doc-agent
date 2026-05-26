# PROJECT_ID="webeye-internal-test"

env GOOGLE_GENAI_USE_VERTEXAI=1 \
  GOOGLE_CLOUD_PROJECT=$PROJECT_ID \
  GOOGLE_CLOUD_LOCATION=global \
  PROJECT_ID=$PROJECT_ID \
  LOCATION=global \
  uv run adk web --host 127.0.0.1 --port 8000
