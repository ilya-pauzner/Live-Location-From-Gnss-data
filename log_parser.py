import datetime
import sys
import os
import csv
import numpy as np
import pandas as pd

from ephemeris_manager import EphemerisManager

parent_directory = os.path.split(os.getcwd())[0]
ephemeris_data_directory = os.path.join(parent_directory, 'data')
sys.path.insert(0, parent_directory)


# Get path to sample file in data directory, which is located in the parent directory of this notebook
input_filepath = os.path.join(parent_directory,'Autonomous-Robotics-Ex0', 'data', 'gnss_log_2024_04_13_19_53_33.txt')

with open(input_filepath) as csvfile:
    reader = csv.reader(csvfile)
    for row in reader:
        if row[0][0] == '#':
            if 'Fix' in row[0]:
                android_fixes = [row[1:]]
            elif 'Raw' in row[0]:
                measurements = [row[1:]]
        else:
            if row[0] == 'Fix':
                android_fixes.append(row[1:])
            elif row[0] == 'Raw':
                measurements.append(row[1:])

# Print the first few rows of each dataframe
# print(android_fixes[:5])
# print(measurements[:5])

# Convert lists to dataframes
android_fixes = pd.DataFrame(android_fixes[1:], columns = android_fixes[0])
measurements = pd.DataFrame(measurements[1:], columns = measurements[0])

# Format satellite IDs
measurements.loc[measurements['Svid'].str.len() == 1, 'Svid'] = '0' + measurements['Svid']
measurements.loc[measurements['ConstellationType'] == '1', 'Constellation'] = 'G'
measurements.loc[measurements['ConstellationType'] == '3', 'Constellation'] = 'R'
measurements['SvName'] = measurements['Constellation'] + measurements['Svid']

# Remove all non-GPS measurements
measurements = measurements.loc[measurements['Constellation'] == 'G']

# Convert columns to numeric representation
measurements['Cn0DbHz'] = pd.to_numeric(measurements['Cn0DbHz'])
measurements['TimeNanos'] = pd.to_numeric(measurements['TimeNanos'])
measurements['FullBiasNanos'] = pd.to_numeric(measurements['FullBiasNanos'])
measurements['ReceivedSvTimeNanos']  = pd.to_numeric(measurements['ReceivedSvTimeNanos'])
measurements['PseudorangeRateMetersPerSecond'] = pd.to_numeric(measurements['PseudorangeRateMetersPerSecond'])
measurements['ReceivedSvTimeUncertaintyNanos'] = pd.to_numeric(measurements['ReceivedSvTimeUncertaintyNanos'])

# A few measurement values are not provided by all phones
# We'll check for them and initialize them with zeros if missing
if 'BiasNanos' in measurements.columns:
    measurements['BiasNanos'] = pd.to_numeric(measurements['BiasNanos'])
else:
    measurements['BiasNanos'] = 0
if 'TimeOffsetNanos' in measurements.columns:
    measurements['TimeOffsetNanos'] = pd.to_numeric(measurements['TimeOffsetNanos'])
else:
    measurements['TimeOffsetNanos'] = 0

print(measurements.columns)

measurements['GpsTimeNanos'] = measurements['TimeNanos'] - (measurements['FullBiasNanos'] - measurements['BiasNanos'])
gpsepoch = datetime.datetime(1980, 1, 6, 0, 0, 0)
measurements['UnixTime'] = pd.to_datetime(measurements['GpsTimeNanos'], utc = True, origin=gpsepoch)
measurements['UnixTime'] = measurements['UnixTime']

# Split data into measurement epochs
measurements['Epoch'] = 0
measurements.loc[measurements['UnixTime'] - measurements['UnixTime'].shift() > datetime.timedelta(milliseconds=200), 'Epoch'] = 1
measurements['Epoch'] = measurements['Epoch'].cumsum()

WEEKSEC = 604800
LIGHTSPEED = 2.99792458e8

# This should account for rollovers since it uses a week number specific to each measurement

measurements['tRxGnssNanos'] = measurements['TimeNanos'] + measurements['TimeOffsetNanos'] - (measurements['FullBiasNanos'].iloc[0] + measurements['BiasNanos'].iloc[0])
measurements['GpsWeekNumber'] = np.floor(1e-9 * measurements['tRxGnssNanos'] / WEEKSEC)
measurements['tRxSeconds'] = 1e-9*measurements['tRxGnssNanos'] - WEEKSEC * measurements['GpsWeekNumber']
measurements['tTxSeconds'] = 1e-9*(measurements['ReceivedSvTimeNanos'] + measurements['TimeOffsetNanos'])
# Calculate pseudorange in seconds
measurements['prSeconds'] = measurements['tRxSeconds'] - measurements['tTxSeconds']

# Conver to meters
measurements['PrM'] = LIGHTSPEED * measurements['prSeconds']
measurements['PrSigmaM'] = LIGHTSPEED * 1e-9 * measurements['ReceivedSvTimeUncertaintyNanos']

manager = EphemerisManager(ephemeris_data_directory)

epoch = 0
num_sats = 0
while num_sats < 5 :
    one_epoch = measurements.loc[(measurements['Epoch'] == epoch) & (measurements['prSeconds'] < 0.1)].drop_duplicates(subset='SvName')
    timestamp = one_epoch.iloc[0]['UnixTime'].to_pydatetime(warn=False)
    one_epoch.set_index('SvName', inplace=True)
    num_sats = len(one_epoch.index)
    epoch += 1

sats = one_epoch.index.unique().tolist()
ephemeris = manager.get_ephemeris(timestamp, sats)
print(timestamp)
print(one_epoch[['UnixTime', 'tTxSeconds', 'GpsWeekNumber']])
