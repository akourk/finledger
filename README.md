# fin

place raw files into "local/data/input/"
run "local/detection.py"
    this will:
    1. process the files in "local/data/input/"
    2. clean the data (conversions, etc)
    3. rename the files to the common naming scheme: "[account]-[start-date]to[end-date].csv"
    4. move these files to "local/data/input/processed/"
    5. create a duplicate file in "local/data/output/[account]/"
    6. read "local/data/output/master/transactions.csv"
        a. append any new values
        b. sort
        c. save
run "local/generate.py"
    this will:
    1. read "local/data/output/master/transactions.csv"
    2. identify all unique holdings and amounts (Symbol,Name,Quantity)
    3. generate, format and save "local/data/output/master/holdings.csv"
    4. read "local/data/historical/close.csv"
    5. compare transaction dates and symbols from transactions.csv and append any new dates and/or symbols
    6. populate missing data with values at close
    7. save to "local/data/historical/close.csv"

to view in browser:
    1. place up-to-date versions of "holdings.csv" and "transactions.csv" in "local/localhtml/data/raw/"
    2. run "local/localhtml/data/generate.py"
        this will:
        a. re-check the data in "local/localhtml/data/raw/holdings.csv" and "local/localhtml/data/raw/transactions.csv"
        b. update "local/localhtml/data/processed/holdings-data.js" and "local/localhtml/data/processed/transactions-data.js"
    3. open "local/localhtml/index.html" in local browser.
