async def upload_files(request: Request, files: list[UploadFile] = File(...) , current_user=Depends(get_current_user)):
    results = []
    for file in files:
        result = await save_file_with_retry(file, current_user)
        results.append(result)
    return JSONResponse(results, status_code=200)


async def save_file_with_retry(file: UploadFile, current_user):
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            file_content = await file.read()
            file_hash = hashlib.md5(file_content).hexdigest()  # Calculate MD5 hash

            #Check if file already exists.  This is an example, adjust as needed for your FileStorage implementation
            if await FileStorage(current_user.user).file_exists(file.filename, file_hash):
                return {"filename": file.filename, "attempt": attempt, "status": "skipped", "message": "File already exists"}

            save_result = await FileStorage(current_user.user).save_file(file, file_hash)
            return {"filename": file.filename, "attempt": attempt, "status": "success", **save_result}
        except Exception as e:
            if attempt == max_retries:
                logger.error(f"Failed to upload {file.filename} after {max_retries} retries: {e}")
                return {"filename": file.filename, "attempt": attempt, "status": "failed", "message": f"Upload failed after multiple retries: {e}"}
            else:
                logger.warning(f"Failed to upload {file.filename}, retrying (attempt {attempt}/{max_retries}): {e}")
                await asyncio.sleep(2**attempt)  # Exponential backoff


import hashlib
import asyncio