# PyMySQL compatibility shim.
# Must run before django.db connects so the MySQLdb driver is available on every platform.
import pymysql

pymysql.version_info = (2, 2, 1, "final", 0)
pymysql.install_as_MySQLdb()
