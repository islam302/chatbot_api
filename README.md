# ChatBot API

Django REST Framework API for the ChatBot platform with WhatsApp integration.

## Stack

- Django 5.x + Django REST Framework
- JWT auth (`djangorestframework-simplejwt` with token blacklist)
- Filtering / pagination via `django-filter`
- OpenAPI 3 schema and Swagger UI via `drf-spectacular`
- Optional RAG layer using LangChain + FAISS + OpenAI
- WhatsApp Cloud API integration

## Project layout

```
ChatBotApi/
├── Conf/                  Django project (settings, urls, wsgi/asgi)
├── Homepage/              Q&A, tree, languages, documents, chat, analytics
│   ├── models.py
│   ├── filters.py
│   ├── permissions.py
│   ├── serializers/       split per resource
│   ├── views/             split per resource
│   ├── services/          rag.py, excel_import.py
│   └── urls.py            DRF router + APIViews
├── WhatsApp/              Meta WhatsApp Cloud API integration
│   ├── models.py
│   ├── serializers.py
│   ├── views.py
│   ├── services/          meta_client.py, conversation.py
│   └── urls.py
├── manage.py
├── requirements.txt
└── .env.example
```

## Getting started

```bash
# 1. Create a virtualenv and install deps
python -m venv .venv
. .venv/Scripts/activate          # Windows
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# edit .env and set SECRET_KEY, OPENAI_API_KEY, WHATSAPP_*, etc.

# 3. Migrate and create a superuser
python manage.py makemigrations Homepage WhatsApp
python manage.py migrate
python manage.py createsuperuser

# 4. Run the dev server
python manage.py runserver
```

## API surface

All endpoints are mounted under `/api/v1/`.

| Endpoint                           | Description                                  |
|------------------------------------|----------------------------------------------|
| `POST /auth/login/`                | Obtain a JWT token pair                      |
| `POST /auth/refresh/`              | Refresh an access token                      |
| `POST /auth/verify/`               | Verify an access token                       |
| `POST /users/register/`            | Register a new user                          |
| `GET  /users/me/`                  | Current authenticated user                   |
| `*    /fixed-questions/`           | Fixed Q&A CRUD + `most-asked` action         |
| `*    /questions/`                 | Dynamic Q&A CRUD + `increment-count`, bulk  |
| `*    /unanswered-questions/`      | Unanswered queue + `assign` action           |
| `*    /question-tree/`             | Tree CRUD + `tree`, `children` actions       |
| `*    /languages/`                 | Available languages                          |
| `*    /documents/`                 | RAG document storage (multipart upload)      |
| `POST /imports/excel/`             | Bulk-import Q&A rows from .xlsx              |
| `POST /chat/`                      | RAG / Q&A chat                               |
| `POST /search/`                    | Cross-resource search                        |
| `GET  /analytics/`                 | Aggregated stats                             |
| `*    /whatsapp/users/`            | Read-only WhatsApp user list                 |
| `*    /whatsapp/sessions/`         | Read-only sessions                           |
| `*    /whatsapp/messages/`         | Read-only message log                        |
| `*    /whatsapp/analytics/`        | Read-only daily analytics                    |
| `POST /whatsapp/send/`             | Send a free-form WhatsApp text               |
| `GET  /whatsapp/webhook/`          | Meta webhook verification                    |
| `POST /whatsapp/webhook/`          | Meta webhook receiver                        |

OpenAPI schema is available at `/api/schema/`, Swagger UI at `/api/docs/`,
and ReDoc at `/api/redoc/`.

## Authentication

Most endpoints require a Bearer JWT:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "..."}'
```

The chat endpoint and the WhatsApp webhook are public.
