#!/bin/bash
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <database-file>"
    exit 1
fi
sqlite3 $1 """
    SELECT 
    created, 
    json_extract(plaid_json, '$.name') , 
    json_extract(plaid_json, '$.amount') 
    FROM 
    transactions 
    ORDER BY created DESC 
    LIMIT 10;
"""