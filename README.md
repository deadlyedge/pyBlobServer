# pyBlobServer

A simple and efficient file server API built with Python, FastAPI, and Tortoise ORM.

## Overview

pyBlobServer is a lightweight and secure file server API that allows users to upload, download, and manage files, it could support especially a frontend developer with a simple api interface.  It provides user authentication, file statistics tracking, cleanup of expired files, rate limiting, and detailed logging.

This project should be deploy on a vps server with a public IP address, and it should be accessible from the internet. If you want to deploy it on a local machine, you should use a service like [ngrok](https://ngrok.com/) to expose your local server to the internet.

This project is NOT aiming to be a full-featured file server, if you need some more performence, you should try some paid blob services, Amazon, Vercel, or Uploadthing have a free plan.

Or you just want to transfer files, in that case, [FilePizza](https://github.com/kernc/filepizza) or [File.io](https://file.io/) maybe better.

Anyway, this project is just for fun and learning, so please don't use it in production, and don't expect it to be stable.  In my opinion, it's best for store some picutres or files for a personal website, or a small team, small project, because without a CDN and some chunk technoligy, it's not suitable for large files or high traffic. and I made a little [frontend UI](https://github.com/deadlyedge/blob-server-ui-next) for it, you could use it as a reference, or just use it directly, it's very simple and easy to use, and it's also open source.

This two project working together for just one goal: Make your VPS investment worthy.


As a hobby programer, I learn programing just for fun and buy a VPS just for show off, or at least at the beginning.  And then I found if I want to safe my files for some more show offs, I can't, I need to pay more for some blob services again.  So I'm very angry and I have to write something to get back my control.

And when I did, I found something fun from it.

So here we are.

This project is not finished but very usable, just for some funciton not finished yet.  But I expect it will save your cost in the future but it's all depends on the project you want to host.

Still, you can consider this is public beta, have some fun!

During my coding works, AI did 80% works somehow, I just drink some coffee and do minor adjustments.  It's amazing.  Time's changed,
people can now do only thinking works, and left 80% for AI.  Pretty cool!

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

All endpoints (except `/health` and `/s/file_id`) require Bearer token authentication. Include the token in the Authorization header:
```
Authorization: Bearer <user_token>
```

### Endpoints

#### User Management

##### GET `/user/{user_id}`
Get user information and statistics.
- Query Parameters:
  - `function`: Optional. Set to "change_token" to generate a new token
- Response: User details including storage usage but token hidden, token will not shown again for security reasons, but you could change it by using `function=change_token`.

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

##### Several Upload Endpoint Added

websocket is fast, demo code at: 
- python: https://github.com/deadlyedge/pyBlobServer/blob/with-websockets/app/test_send_file.py
- react/nextjs: https://github.com/deadlyedge/blob-server-ui-next/blob/master/components/uploadZone.tsx
  

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
    - `expired`: Delete files not accessed for 90 days (not been tested yet)
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
├── uploads/         # File storage directory (could be built automaticly)
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
or, here's a docker-compose.example.yml file for you to use, you could change the port and volume path to your own.

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
- ~~add batch upload functionality and if any of files failed, return status code of 207.~~
- do more testing on every api endpoints
- think of do something like tus protocal do, they're good on something but bad to make them happen, sadly.