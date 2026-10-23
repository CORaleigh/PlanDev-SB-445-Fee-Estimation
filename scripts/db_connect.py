import pyodbc
import logging
import pandas as pd
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TrustedSQLServerConnection:
    def __init__(self, server, database):
        self.server = server
        self.database = database
        self.connection = None
        self.cursor = None

        def get_installed_odbc_drivers():
            """Retrieve a list of installed ODBC drivers."""
            drivers = pyodbc.drivers()
            return drivers

        def select_best_sql_server_driver(drivers):
            """Select the best available SQL Server ODBC driver from the list."""
            sql_server_drivers = [driver for driver in drivers if 'SQL Server' in driver or 'SQLSERVER' in driver.upper()]
            if not sql_server_drivers:
                return None
            # Compare numeric versions, not installation order.
            def driver_sort_key(name):
                import re
                match = re.search(r'ODBC Driver (\d+)', name)
                return int(match.group(1)) if match else 0
            odbc_drivers = [d for d in sql_server_drivers if 'ODBC Driver' in d]
            if odbc_drivers:
                best = sorted(odbc_drivers, key=driver_sort_key, reverse=True)[0]
                return best
            return sql_server_drivers[0]

        installed_drivers = get_installed_odbc_drivers()
        if not installed_drivers:
            logger.warning("No ODBC drivers are installed.")
            exit(1)
        else:
            logger.info("Installed ODBC drivers:")
            for driver in installed_drivers:
                logger.info(f"- {driver}")

            best_driver = select_best_sql_server_driver(installed_drivers)
            if not best_driver:
                logger.warning("No suitable SQL Server ODBC driver found.")
                exit(1)
            else:
                logger.info(f"Using SQL Server ODBC driver: {best_driver}")
        self.driver = best_driver

        self.connection_string = (
            f"DRIVER={{{best_driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"Trusted_Connection=yes;"
            f"Encrypt=no;"
            f"TrustServerCertificate=yes;"
        )

    def connect(self):
        """Establish a connection to the SQL Server database."""
        try:
            self.connection = pyodbc.connect(self.connection_string)
            self.cursor = self.connection.cursor()
        except pyodbc.Error as e:
            sqlstate = e.args[0]
            message = e.args[1]
            logger.error(f"Error connecting to the database: SQLState={sqlstate}, Message={message}")

    def test_connection(self):
        """Test the connection by executing a simple query."""
        try:
            self.cursor.execute("SELECT name FROM sys.databases")
            row = self.cursor.fetchone()
            if row:
                logger.info(f"Connected to database: {row[0]}")
            else:
                logger.info("No data returned from the query.")
        except pyodbc.Error as e:
            sqlstate = e.args[0]
            message = e.args[1]
            logger.error(f"Error connecting to the database: SQLState={sqlstate}, Message={message}")

    def close(self):
        """Close the database connection."""
        if self.connection:
            self.connection.close()
            self.cursor = None
            self.connection = None
            logger.info("Connection closed.")
        else:
            logger.info("No connection to close.")

    def execute_query_sets(self, query):
        """Execute SQL and return every tabular result set as DataFrames."""
        if not self.connection:
            raise Exception("Connection is not established. Call connect() first.")
        try:
            if os.path.isfile(query):
                with open(query, "r", encoding="utf-8-sig") as file:
                    query = file.read()

            self.cursor.execute(query)
            result_sets = []

            while True:
                if self.cursor.description is not None:
                    columns = [
                        column[0]
                        for column in self.cursor.description
                    ]
                    data = self.cursor.fetchall()
                    result_sets.append(
                        pd.DataFrame.from_records(data, columns=columns)
                    )

                if not self.cursor.nextset():
                    break

            if not result_sets:
                logger.error("No result set returned by the query.")
            return result_sets
        except pyodbc.Error as e:
            sqlstate = e.args[0]
            message = e.args[1]
            logger.error(
                "Error executing query: "
                f"SQLState={sqlstate}, Message={message}"
            )
            return []

    # Backward-compatible single-result helper used by existing scripts.
    def execute_query(self, query):
        """Execute SQL and return the first tabular result set."""
        result_sets = self.execute_query_sets(query)
        return result_sets[0] if result_sets else None

if __name__ == "__main__":
    db = TrustedSQLServerConnection(server='my_server', database='my_database')
    db.connect()
    db.test_connection()
    db.close()
