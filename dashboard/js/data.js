/**
 * Data Access Layer
 * Handles data normalization and lazy loading of portfolio data
 */

// ==========================================
// Data Normalization Functions
// ==========================================

function normalizeHoldings(data) {
    if (!data) return [];
    return data.map(function (h) {
        return {
            account: h.Account,
            symbol: h.Symbol,
            quantity: h.Quantity,
            cost_basis: h.CostBasis,
            current_price: h.CurrentPrice,
            current_value: h.CurrentValue,
            unrealized_gain: h.UnrealizedGain,
            unrealized_gain_pct: h.UnrealizedGainPct,
            change_7d: h.Change7D,
            change_30d: h.Change30D,
            sector: h.Sector || 'Unknown',
            first_buy_date: h.FirstPurchaseDate || null
        };
    });
}

function normalizeAccounts(data) {
    if (!data) return [];
    return data.map(function (a) {
        return {
            account: a.Account,
            total_value: a.TotalValue,
            total_cost_basis: a.TotalCostBasis,
            unrealized_gain: a.TotalUnrealizedGain,
            unrealized_gain_pct: a.UnrealizedGainPct
        };
    });
}

function normalizeIncome(data) {
    if (!data) return [];
    return data.map(function (r) {
        return {
            year: r.Year,
            dividends: r.Dividend || 0,
            interest: r.Interest || 0,
            staking: r.Staking || 0,
            other: r.StockLending || 0,
            total: r.Total || 0
        };
    });
}

function normalizeHistory(data) {
    if (!data || data.length === 0) return [];

    var firstRow = data[0];
    var accounts = [];
    var excludeKeys = ['Date', 'TotalValue', 'NumPositions', 'SP500', 'TWR', 'PeriodReturn', 'TotalInvested', 'WhatIfSP500'];

    for (var key in firstRow) {
        if (excludeKeys.indexOf(key) === -1) {
            accounts.push(key);
        }
    }

    return data.map(function (h) {
        var result = {
            date: h.Date,
            totalValue: h.TotalValue,
            totalInvested: h.TotalInvested || 0,
            sp500: h.SP500 || null,
            twr: h.TWR || 0,
            periodReturn: h.PeriodReturn || 0,
            whatIfSP500: h.WhatIfSP500 || null,
            accounts: {}
        };

        for (var i = 0; i < accounts.length; i++) {
            result.accounts[accounts[i]] = h[accounts[i]] || 0;
        }

        return result;
    });
}

function normalizeCorporateActions(data) {
    if (!data) return [];
    return data.map(function (action) {
        return {
            date: action.Date,
            type: action.Type,
            symbol: action.Symbol,
            account: action.Account,
            details: action.Details || '',
            shares: action.Shares || 0,
            cash: action.Cash || 0,
            cusip: action.CUSIP || ''
        };
    });
}

// ==========================================
// Lazy-Loaded Data Accessors
// ==========================================

var _holdingsDetail = null;
var _accountSummary = null;
var _incomeByYear = null;
var _historicalHoldings = null;
var _masterTransactions = null;
var _cashBalances = null;

function getHoldingsDetail() {
    if (_holdingsDetail === null && typeof holdingsDetailData !== 'undefined') {
        _holdingsDetail = normalizeHoldings(holdingsDetailData);
    }
    return _holdingsDetail || [];
}

function getAccountSummary() {
    if (_accountSummary === null && typeof accountSummaryData !== 'undefined') {
        _accountSummary = normalizeAccounts(accountSummaryData);
    }
    return _accountSummary || [];
}

function getIncomeByYear() {
    if (_incomeByYear === null && typeof incomeByYearData !== 'undefined') {
        _incomeByYear = normalizeIncome(incomeByYearData);
    }
    return _incomeByYear || [];
}

function getHistoricalHoldings() {
    if (_historicalHoldings === null && typeof historicalHoldingsData !== 'undefined') {
        _historicalHoldings = normalizeHistory(historicalHoldingsData);
    }
    return _historicalHoldings || [];
}

function getCashBalances() {
    if (_cashBalances === null && typeof cashBalancesData !== 'undefined') {
        _cashBalances = cashBalancesData.map(function (c) {
            return {
                account: c.Account,
                balance: c.CurrentBalance,
                deposits: c.TotalDeposits,
                withdrawals: c.TotalWithdrawals,
                netContributions: c.NetContributions,
                totalInterest: c.TotalInterest,
                returnPct: c.ReturnPct
            };
        });
    }
    return _cashBalances || [];
}

function getMasterTransactions() {
    if (_masterTransactions === null && typeof masterTransactionsData !== 'undefined') {
        _masterTransactions = masterTransactionsData.map(function (t) {
            return {
                date: t.Date,
                account: t.Account,
                symbol: t.Symbol,
                action: t.Action,
                quantity: t.Quantity,
                price: t.Price,
                fee: t.Fee,
                amount: t.Amount,
                note: t.Note,
                runningBalance: t.RunningBalance,
                runningValue: t.RunningValue
            };
        });
    }
    return _masterTransactions || [];
}

function getHistoricalAccounts() {
    if (typeof historicalHoldingsData === 'undefined' || historicalHoldingsData.length === 0) {
        return [];
    }

    var firstRow = historicalHoldingsData[0];
    var accounts = [];
    var excludeKeys = ['Date', 'TotalValue', 'NumPositions', 'SP500', 'TWR', 'PeriodReturn', 'TotalInvested', 'WhatIfSP500'];

    for (var key in firstRow) {
        if (excludeKeys.indexOf(key) === -1) {
            accounts.push(key);
        }
    }

    return accounts.sort();
}

function getTransactionsForHolding(symbol, account) {
    var allTx = getMasterTransactions();
    return allTx.filter(function (t) {
        return t.symbol === symbol && t.account === account;
    }).sort(function (a, b) {
        return b.date.localeCompare(a.date);
    });
}

function getRetirementData() {
    if (typeof retirementData !== 'undefined') {
        return retirementData;
    }
    return null;
}

function getCorporateActionsData() {
    if (typeof corporateActionsData !== 'undefined') {
        return normalizeCorporateActions(corporateActionsData);
    }
    return [];
}
