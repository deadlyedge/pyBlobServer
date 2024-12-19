from tortoise import fields, models

class UsersInfo(models.Model):
    user = fields.CharField(max_length=255, primary_key=True)
    token = fields.CharField(max_length=255, db_index=True)  # Added index
    total_size = fields.IntField(default=0)
    total_upload_times = fields.IntField(default=0)
    total_upload_byte = fields.IntField(default=0)
    total_download_times = fields.IntField(default=0)
    total_download_byte = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    last_upload_at = fields.DatetimeField(null=True)
    last_download_at = fields.DatetimeField(null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            "token",  # Index for faster token lookups
        ]


class FileInfo(models.Model):
    file_id = fields.CharField(max_length=255, primary_key=True)
    user = fields.ForeignKeyField(
        "models.UsersInfo", related_name="files", db_index=True
    )
    file_name = fields.CharField(max_length=255)
    file_size = fields.IntField()
    upload_at = fields.DatetimeField(auto_now_add=True)
    download_times = fields.IntField(default=0)
    last_download_at = fields.DatetimeField(null=True)

    class Meta:
        ordering = ["-upload_at"]
        indexes = [
            ("user_id", "upload_at"),  # Composite index for user's file queries
            ("user_id", "last_download_at"),  # For expired file queries
        ]
