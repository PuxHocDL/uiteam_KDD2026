import duckdb
import os
import json
from typing import Any

def execute_duckdb_sql(context_root: str, sql: str, limit: int = 500) -> dict[str, Any]:
    """Execute a read-only SQL query using DuckDB, allowing queries directly on CSV/JSON."""
    try:
        # Create an in-memory duckdb connection
        conn = duckdb.connect(database=':memory:')
        
        # Set working directory to context root so paths like 'csv/races.csv' work
        original_cwd = os.getcwd()
        os.chdir(context_root)
        
        try:
            # We enforce a limit to prevent massive outputs
            safe_sql = f"SELECT * FROM ({sql}) LIMIT {limit}"
            result_df = conn.execute(safe_sql).df()
            
            # Format as list of dicts for safety and a string for observation
            output_str = result_df.to_string()
            if len(output_str) > 8000:
                output_str = output_str[:4000] + "\n...[TRUNCATED]...\n" + output_str[-4000:]
                
            return {
                "success": True,
                "rows_returned": len(result_df),
                "columns": result_df.columns.tolist(),
                "output": output_str
            }
        finally:
            os.chdir(original_cwd)
            conn.close()
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
