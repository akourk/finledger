"""
Monte Carlo Simulation Module
=============================
Runs Monte Carlo simulations to project portfolio growth with probability ranges.
Useful for retirement planning and understanding risk.
"""

import logging
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default simulation parameters
DEFAULT_SIMULATIONS = 1000
DEFAULT_YEARS = 30
CONFIDENCE_LEVELS = [10, 25, 50, 75, 90]  # Percentiles to report


def estimate_historical_stats(historical_df: pd.DataFrame) -> Dict[str, float]:
    """
    Estimate mean return and volatility from historical portfolio data.
    
    Args:
        historical_df: DataFrame with Date and TotalValue columns
    
    Returns:
        Dict with annual mean return and volatility (std dev)
    """
    if historical_df.empty or 'TotalValue' not in historical_df.columns:
        # Default assumptions if no historical data
        return {
            'annual_return': 0.07,  # 7% default
            'annual_volatility': 0.15,  # 15% default
            'data_points': 0
        }
    
    df = historical_df.copy()
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date')
    
    # Calculate daily returns
    df['Return'] = df['TotalValue'].pct_change()
    returns = df['Return'].dropna()
    
    if len(returns) < 30:
        # Not enough data, use defaults
        return {
            'annual_return': 0.07,
            'annual_volatility': 0.15,
            'data_points': len(returns)
        }
    
    # Annualize (assuming ~252 trading days)
    daily_mean = returns.mean()
    daily_std = returns.std()
    
    annual_return = daily_mean * 252
    annual_volatility = daily_std * np.sqrt(252)
    
    return {
        'annual_return': annual_return,
        'annual_volatility': annual_volatility,
        'data_points': len(returns)
    }


def run_monte_carlo_simulation(
    starting_value: float,
    annual_contribution: float = 0,
    years: int = DEFAULT_YEARS,
    mean_return: float = 0.07,
    volatility: float = 0.15,
    num_simulations: int = DEFAULT_SIMULATIONS,
    inflation_rate: float = 0.025
) -> Dict:
    """
    Run Monte Carlo simulation for portfolio growth.
    
    Uses geometric Brownian motion model:
    S(t+1) = S(t) * exp((μ - σ²/2)*dt + σ*√dt*Z)
    
    where Z is a standard normal random variable.
    
    Args:
        starting_value: Current portfolio value
        annual_contribution: Annual savings to add (applied at year end)
        years: Number of years to project
        mean_return: Expected annual return (e.g., 0.07 for 7%)
        volatility: Annual standard deviation (e.g., 0.15 for 15%)
        num_simulations: Number of Monte Carlo paths
        inflation_rate: Annual inflation rate for real return calculation
    
    Returns:
        Dict with simulation results including percentile paths
    """
    np.random.seed(42)  # For reproducibility
    
    # Time parameters (monthly steps)
    steps_per_year = 12
    total_steps = years * steps_per_year
    dt = 1 / steps_per_year
    
    # Monthly contribution
    monthly_contribution = annual_contribution / 12
    
    # Adjust parameters for monthly steps
    monthly_return = mean_return / 12
    monthly_vol = volatility / np.sqrt(12)
    
    # Initialize simulation array
    # Shape: (num_simulations, total_steps + 1)
    paths = np.zeros((num_simulations, total_steps + 1))
    paths[:, 0] = starting_value
    
    # Run simulation
    for t in range(1, total_steps + 1):
        # Generate random returns
        z = np.random.standard_normal(num_simulations)
        
        # Geometric Brownian motion step
        drift = (monthly_return - 0.5 * monthly_vol ** 2)
        diffusion = monthly_vol * z
        
        paths[:, t] = paths[:, t-1] * np.exp(drift + diffusion) + monthly_contribution
    
    # Calculate percentiles at each time step
    percentiles = {}
    for p in CONFIDENCE_LEVELS:
        percentiles[f'p{p}'] = np.percentile(paths, p, axis=0)
    
    # Also calculate mean path
    percentiles['mean'] = np.mean(paths, axis=0)
    
    # Extract yearly values for easier display
    yearly_indices = [i * steps_per_year for i in range(years + 1)]
    yearly_values = {
        key: [round(values[i], 2) for i in yearly_indices]
        for key, values in percentiles.items()
    }
    
    # Calculate final value statistics
    final_values = paths[:, -1]
    
    # Probability calculations
    prob_double = np.mean(final_values >= starting_value * 2) * 100
    prob_triple = np.mean(final_values >= starting_value * 3) * 100
    prob_million = np.mean(final_values >= 1_000_000) * 100 if starting_value < 1_000_000 else None
    prob_loss = np.mean(final_values < starting_value) * 100
    
    # Calculate inflation-adjusted (real) final values
    inflation_factor = (1 + inflation_rate) ** years
    real_final_values = final_values / inflation_factor
    
    result = {
        'parameters': {
            'starting_value': starting_value,
            'annual_contribution': annual_contribution,
            'years': years,
            'mean_return': round(mean_return * 100, 1),
            'volatility': round(volatility * 100, 1),
            'num_simulations': num_simulations,
            'inflation_rate': round(inflation_rate * 100, 1)
        },
        'yearly_values': yearly_values,
        'years_list': list(range(years + 1)),
        'final_statistics': {
            'median': round(np.median(final_values), 2),
            'mean': round(np.mean(final_values), 2),
            'p10': round(np.percentile(final_values, 10), 2),
            'p25': round(np.percentile(final_values, 25), 2),
            'p75': round(np.percentile(final_values, 75), 2),
            'p90': round(np.percentile(final_values, 90), 2),
            'min': round(np.min(final_values), 2),
            'max': round(np.max(final_values), 2),
            'std_dev': round(np.std(final_values), 2)
        },
        'real_final_statistics': {
            'median': round(np.median(real_final_values), 2),
            'p10': round(np.percentile(real_final_values, 10), 2),
            'p90': round(np.percentile(real_final_values, 90), 2)
        },
        'probabilities': {
            'double': round(prob_double, 1),
            'triple': round(prob_triple, 1),
            'million': round(prob_million, 1) if prob_million else None,
            'loss': round(prob_loss, 1)
        }
    }
    
    return result


def run_scenario_analysis(
    starting_value: float,
    annual_contribution: float = 0,
    years: int = DEFAULT_YEARS
) -> Dict:
    """
    Run Monte Carlo simulations for multiple market scenarios.
    
    Scenarios:
    - Bear: Low returns, high volatility
    - Conservative: Below-average returns
    - Moderate: Historical average
    - Bullish: Above-average returns
    - Optimistic: High returns, low volatility
    
    Args:
        starting_value: Current portfolio value
        annual_contribution: Annual savings
        years: Projection horizon
    
    Returns:
        Dict with results for each scenario
    """
    scenarios = {
        'bear': {'mean_return': 0.03, 'volatility': 0.25, 'label': 'Bear Market'},
        'conservative': {'mean_return': 0.05, 'volatility': 0.12, 'label': 'Conservative'},
        'moderate': {'mean_return': 0.07, 'volatility': 0.15, 'label': 'Moderate'},
        'bullish': {'mean_return': 0.09, 'volatility': 0.18, 'label': 'Bullish'},
        'optimistic': {'mean_return': 0.11, 'volatility': 0.12, 'label': 'Optimistic'}
    }
    
    results = {}
    
    for scenario_key, params in scenarios.items():
        sim_result = run_monte_carlo_simulation(
            starting_value=starting_value,
            annual_contribution=annual_contribution,
            years=years,
            mean_return=params['mean_return'],
            volatility=params['volatility'],
            num_simulations=500  # Fewer simulations per scenario for speed
        )
        
        results[scenario_key] = {
            'label': params['label'],
            'return': round(params['mean_return'] * 100, 1),
            'volatility': round(params['volatility'] * 100, 1),
            'median_final': sim_result['final_statistics']['median'],
            'p10_final': sim_result['final_statistics']['p10'],
            'p90_final': sim_result['final_statistics']['p90']
        }
    
    return results


def calculate_safe_withdrawal(
    portfolio_value: float,
    years: int = 30,
    success_rate: float = 0.95,
    mean_return: float = 0.07,
    volatility: float = 0.15,
    num_simulations: int = 1000
) -> Dict:
    """
    Calculate the safe withdrawal rate for retirement.
    
    Finds the maximum annual withdrawal that maintains a given success rate
    (probability of not running out of money).
    
    Args:
        portfolio_value: Current portfolio value
        years: Retirement duration in years
        success_rate: Target probability of success (e.g., 0.95 for 95%)
        mean_return: Expected annual return
        volatility: Annual volatility
        num_simulations: Number of simulations
    
    Returns:
        Dict with safe withdrawal rate and amount
    """
    # Binary search for safe withdrawal rate
    low_rate = 0.02  # 2%
    high_rate = 0.10  # 10%
    
    np.random.seed(42)
    
    for _ in range(20):  # Max iterations
        test_rate = (low_rate + high_rate) / 2
        annual_withdrawal = portfolio_value * test_rate
        
        # Simulate with this withdrawal rate
        successes = 0
        
        for sim in range(num_simulations):
            balance = portfolio_value
            
            for year in range(years):
                # Apply return
                z = np.random.standard_normal()
                annual_return = np.exp((mean_return - 0.5 * volatility**2) + volatility * z)
                balance = balance * annual_return
                
                # Withdraw
                balance -= annual_withdrawal
                
                if balance <= 0:
                    break
            
            if balance > 0:
                successes += 1
        
        actual_success_rate = successes / num_simulations
        
        if actual_success_rate >= success_rate:
            low_rate = test_rate
        else:
            high_rate = test_rate
        
        if high_rate - low_rate < 0.001:
            break
    
    safe_rate = low_rate
    
    return {
        'safe_withdrawal_rate': round(safe_rate * 100, 2),
        'annual_withdrawal': round(portfolio_value * safe_rate, 2),
        'monthly_withdrawal': round(portfolio_value * safe_rate / 12, 2),
        'success_rate': round(success_rate * 100, 0),
        'years': years
    }


def generate_monte_carlo_report(
    portfolio_value: float,
    annual_contribution: float,
    historical_df: pd.DataFrame,
    years_to_retirement: int = 30,
    retirement_data: Dict = None
) -> Dict:
    """
    Generate comprehensive Monte Carlo projection report.
    
    Args:
        portfolio_value: Current portfolio value
        annual_contribution: Annual savings/contribution
        historical_df: Historical portfolio values for volatility estimation
        years_to_retirement: Years until retirement
        retirement_data: Additional retirement parameters
    
    Returns:
        Dict with full Monte Carlo analysis
    """
    print(f"\n19. Running Monte Carlo simulations...")
    
    # Estimate historical volatility if available
    historical_stats = estimate_historical_stats(historical_df)
    
    # Use historical volatility if available, otherwise defaults
    if historical_stats['data_points'] >= 30:
        volatility = min(historical_stats['annual_volatility'], 0.30)  # Cap at 30%
        print(f"   Using historical volatility: {volatility*100:.1f}%")
    else:
        volatility = 0.15
        print(f"   Using default volatility: {volatility*100:.1f}%")
    
    # Main simulation with moderate assumptions
    print(f"   Running {DEFAULT_SIMULATIONS} simulations over {years_to_retirement} years...")
    main_simulation = run_monte_carlo_simulation(
        starting_value=portfolio_value,
        annual_contribution=annual_contribution,
        years=years_to_retirement,
        mean_return=0.07,
        volatility=volatility,
        num_simulations=DEFAULT_SIMULATIONS
    )
    
    # Scenario analysis
    print(f"   Running scenario analysis...")
    scenarios = run_scenario_analysis(
        starting_value=portfolio_value,
        annual_contribution=annual_contribution,
        years=years_to_retirement
    )
    
    # Safe withdrawal rate calculation (for post-retirement)
    print(f"   Calculating safe withdrawal rate...")
    withdrawal_analysis = calculate_safe_withdrawal(
        portfolio_value=main_simulation['final_statistics']['median'],
        years=30,  # 30 years in retirement
        success_rate=0.95
    )
    
    result = {
        'generated_at': datetime.now().isoformat(),
        'current_portfolio': portfolio_value,
        'annual_contribution': annual_contribution,
        'years_to_retirement': years_to_retirement,
        'historical_volatility': round(historical_stats['annual_volatility'] * 100, 1),
        'historical_data_points': historical_stats['data_points'],
        'main_simulation': main_simulation,
        'scenarios': scenarios,
        'withdrawal_analysis': withdrawal_analysis
    }
    
    # Summary statistics
    median_final = main_simulation['final_statistics']['median']
    p10_final = main_simulation['final_statistics']['p10']
    p90_final = main_simulation['final_statistics']['p90']
    
    print(f"   Projected portfolio in {years_to_retirement} years:")
    print(f"     10th percentile: ${p10_final:,.0f}")
    print(f"     Median (50th):   ${median_final:,.0f}")
    print(f"     90th percentile: ${p90_final:,.0f}")
    
    return result
