# pyBlobServer

A simple and efficient file server API built with Python, FastAPI, and Tortoise ORM.

## Features

- 🚀 Fast file upload and download
- 🔐 User authentication with token-based security
- 📊 File and user statistics tracking
- 💾 Automatic file size limits and quota management
- 🗑️ Automatic cleanup of expired files
- 🎯 Rate limiting to prevent abuse
- 📝 Detailed logging of all operations

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/pyBlobServer.git
cd pyBlobServer
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment variables in `.env`:
```env
SECRET_KEY=your_secret_key
BASE_URL=http://localhost:8000
BASE_FOLDER=./uploads
ALLOWED_USERS=user1,user2,user3
DEFAULT_SHORT_PATH_LENGTH=8
FILE_SIZE_LIMIT_MB=10
TOTAL_SIZE_LIMIT_MB=500
DATABASE_URL=sqlite://./uploads/blobserver.db
CACHE_TTL=300
```

4. Run the server:
```bash
uvicorn app.main:app --reload
```

## API Documentation

### Authentication

All endpoints (except `/health`) require Bearer token authentication. Include the token in the Authorization header:
```
Authorization: Bearer <user_token>
```

### Endpoints

#### User Management

##### GET `/user/{user_id}`
Get user information and statistics.
- Query Parameters:
  - `function`: Optional. Set to "change_token" to generate a new token
- Response: User details including storage usage and token

#### File Operations

##### POST `/upload`
Upload a new file.
- Body: Form data with file
- Response:
```json
{
    "file_id": "abc123",
    "file_url": "http://localhost:8000/s/abc123",
    "show_image": "http://localhost:8000/s/abc123?output=html",
    "available_space": "490.5 MB"
}
```

##### GET `/s/{file_id}`
Download or view a file.
- Query Parameters:
  - `output`: Optional. Values:
    - `file` (default): Download file
    - `html`: View in browser (for images)
    - `json`: Get file metadata
- Response: File content or metadata

##### GET `/list`
List all files for current user.
- Response: Array of file metadata

##### DELETE `/delete/{file_id}`
Delete a specific file.
- Response:
```json
{
    "message": "File deleted"
}
```

##### DELETE `/delete_all`
Delete all files or expired files.
- Query Parameters:
  - `confirm`: Must be "yes" to confirm deletion
  - `function`: Optional. Values:
    - `all` (default): Delete all files
    - `expired`: Delete files not accessed for 90 days
- Response:
```json
{
    "message": "All files deleted"
}
```

### File Limits

- Maximum file size: 10MB (configurable)
- Total storage per user: 500MB (configurable)
- File retention: Files not downloaded for 90 days are considered expired

### Error Responses

All error responses follow this format:
```json
{
    "detail": "Error message"
}
```

Common status codes:
- 400: Bad Request
- 401: Unauthorized
- 403: Forbidden
- 404: Not Found
- 413: Request Entity Too Large
- 429: Too Many Requests
- 500: Internal Server Error

## Development

### Project Structure
```
pyBlobServer/
├── app/
│   ├── main.py      # FastAPI application and routes
│   └── models.py    # Database models and business logic
├── uploads/         # File storage directory
├── .env            # Environment configuration
├── requirements.txt # Python dependencies
└── README.md       # Documentation
```

### Running Tests
```bash
pytest
```

### Docker Support
```bash
docker build -t pyblobserver .
docker run -p 8000:8000 pyblobserver
```

## License

MIT License

## Contributing

1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

# 📝 TODO

- errors return Json response