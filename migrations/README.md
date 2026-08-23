# ترحيلات قاعدة البيانات

يدير Alembic مخطط PostgreSQL. لا تستخدم `Base.metadata.create_all()` في بيئة منشورة؛ فهي مخصصة للاختبارات المعزولة فقط.

```powershell
alembic upgrade head
alembic current
alembic downgrade -1
```

يقرأ Alembic الاتصال من `AWN_DATABASE_URL` عند وجوده، وإلا يستخدم القيمة المحلية في `alembic.ini`.
