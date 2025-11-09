import csv
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from scipy.optimize import least_squares
from ephemeris_manager import EphemerisManager
import simplekml
from gnss_lib_py.utils.constants import CONSTELLATION_ANDROID, CONSTELLATION_CHARS

class Parser:
    LIGHTSPEED = 2.99792458e8
    WEEKSEC = 604800
    
    def __init__(self, ephemeris_data_directory):
        self.manager = EphemerisManager(ephemeris_data_directory)
        self.gpsepoch = datetime(1980, 1, 6, 0, 0, 0)
    
    def open_file(self, filepath):
        with open(filepath) as csvfile:
            reader = csv.DictReader(csvfile)
            measurements = [row for row in reader]
        measurements = pd.DataFrame(measurements)
        return measurements

    def formatDF(self, measurements):
        if measurements.empty:
            print("No measurements to process.")
            return measurements
        
        required_columns = ['svid', 'constellationType', 'timeNanos', 'fullBiasNanos', 'receivedSvTimeNanos', 
                            'pseudorangeRateMetersPerSecond', 'receivedSvTimeUncertaintyNanos', 'cn0DbHz']
        
        for column in required_columns:
            if column not in measurements.columns:
                print(f"Missing required column: {column}")
                return pd.DataFrame()
        
        measurements['Svid'] = measurements['svid'].apply(lambda x: f"{int(x):02d}")

        # ? = unknown = 0
        # G = gps = 1
        # S = sbas = 2
        # R = glonass = 3
        # J = qzss = 4
        # C = beidou = 5
        # E = galileo = 6
        # I = irnss = 7

        fromNameToLetter = {v: k for k, v in CONSTELLATION_CHARS.items()}
        fromNumberToName = CONSTELLATION_ANDROID

        measurements.loc[:, 'Constellation'] = measurements.loc[:, 'Constellation'].map(fromNumberToName).map(fromNameToLetter)
        measurements['satPRN'] = measurements['Constellation'] + measurements['Svid']

        measurements['Cn0DbHz'] = pd.to_numeric(measurements['cn0DbHz'])
        measurements['TimeNanos'] = pd.to_numeric(measurements['timeNanos'])
        measurements['FullBiasNanos'] = pd.to_numeric(measurements['fullBiasNanos'])
        measurements['ReceivedSvTimeNanos'] = pd.to_numeric(measurements['receivedSvTimeNanos'])
        measurements['PseudorangeRateMetersPerSecond'] = pd.to_numeric(measurements['pseudorangeRateMetersPerSecond'])
        measurements['ReceivedSvTimeUncertaintyNanos'] = pd.to_numeric(measurements['receivedSvTimeUncertaintyNanos'])
        measurements['BiasNanos'] = pd.to_numeric(measurements.get('biasNanos', 0))
        measurements['TimeOffsetNanos'] = pd.to_numeric(measurements.get('timeOffsetNanos', 0))

        if measurements.empty or measurements['FullBiasNanos'].isna().any() or measurements['BiasNanos'].isna().any():
            print("Missing bias data in measurements.")
            return pd.DataFrame()
        
        measurements['GpsTimeNanos'] = measurements['TimeNanos'] - (measurements['FullBiasNanos'] - measurements['BiasNanos'])
        measurements['UnixTime'] = pd.to_datetime(measurements['GpsTimeNanos'], utc=True, origin=self.gpsepoch)

        measurements['Epoch'] = 0
        measurements.loc[measurements['UnixTime'] - measurements['UnixTime'].shift() > timedelta(milliseconds=200), 'Epoch'] = 1
        measurements['Epoch'] = measurements['Epoch'].cumsum()

        if len(measurements) > 0:
            measurements['gnss_receive_time_nanoseconds'] = measurements['TimeNanos'] + measurements['TimeOffsetNanos'] - (measurements['FullBiasNanos'].iloc[0] + measurements['BiasNanos'].iloc[0])
        else:
            measurements['gnss_receive_time_nanoseconds'] = np.nan

        measurements['GpsWeekNumber'] = np.floor(1e-9 * measurements['gnss_receive_time_nanoseconds'] / self.WEEKSEC)
        measurements['time_since_reference'] = 1e-9 * measurements['gnss_receive_time_nanoseconds'] - self.WEEKSEC * measurements['GpsWeekNumber']
        measurements['transmit_time_seconds'] = 1e-9 * (measurements['ReceivedSvTimeNanos'] + measurements['TimeOffsetNanos'])

        measurements['pseudorange_seconds'] = measurements['time_since_reference'] - measurements['transmit_time_seconds']
        measurements['Pseudorange_Measurement'] = self.LIGHTSPEED * measurements['pseudorange_seconds']

        return measurements

    def generate_epoch(self, measurements):
        epoch = 0
        num_sats = 0
        max_epoch = measurements['Epoch'].max()
        one_epoch = pd.DataFrame()
        
        while num_sats < 5 and epoch <= max_epoch:
            one_epoch = measurements.loc[(measurements['Epoch'] == epoch) & (measurements['pseudorange_seconds'] < 0.1)].drop_duplicates(subset='satPRN')
            if one_epoch.empty:
                epoch += 1
                continue
            timestamp = one_epoch.iloc[0]['UnixTime'].to_pydatetime(warn=False)
            one_epoch.set_index('satPRN', inplace=True)
            num_sats = len(one_epoch.index)
            epoch += 1

        if one_epoch.empty:
            return pd.DataFrame(), pd.DataFrame()

        sats = one_epoch.index.unique().tolist()
        ephemeris = self.manager.get_ephemeris(timestamp, sats)

        return one_epoch, ephemeris
    
    def calculate_satellite_position(self, ephemeris, transmit_time):
        earth_gravity = 3.986005e14
        Earth_angular_velocity = 7.2921151467e-5
        relativistic_correction_factor = -4.442807633e-10
        sv_position = pd.DataFrame()
        sv_position['satPRN'] = ephemeris.index
        sv_position.set_index('satPRN', inplace=True)
        sv_position['GPS time'] = transmit_time - ephemeris['t_oe']
        A = ephemeris['sqrtA'].pow(2)
        n_0 = np.sqrt(earth_gravity / A.pow(3))
        n = n_0 + ephemeris['deltaN']
        M_k = ephemeris['M_0'] + n * sv_position['GPS time']
        E_k = M_k
        err = pd.Series(data=[1] * len(sv_position.index))
        i = 0
        while err.abs().min() > 1e-8 and i < 10:
            new_vals = M_k + ephemeris['e'] * np.sin(E_k)
            err = new_vals - E_k
            E_k = new_vals
            i += 1

        sinE_k = np.sin(E_k)
        cosE_k = np.cos(E_k)
        delT_r = relativistic_correction_factor * ephemeris['e'] * ephemeris['sqrtA'] * sinE_k
        delT_oc = transmit_time - ephemeris['t_oc']
        sv_position['Sat.bias'] = ephemeris['SVclockBias'] + ephemeris['SVclockDrift'] * delT_oc + ephemeris['SVclockDriftRate'] * delT_oc.pow(2)

        v_k = np.arctan2(np.sqrt(1 - ephemeris['e'].pow(2)) * sinE_k, (cosE_k - ephemeris['e']))
        Phi_k = v_k + ephemeris['omega']
        sin2Phi_k = np.sin(2 * Phi_k)
        cos2Phi_k = np.cos(2 * Phi_k)

        du_k = ephemeris['C_us'] * sin2Phi_k + ephemeris['C_uc'] * cos2Phi_k
        dr_k = ephemeris['C_rs'] * sin2Phi_k + ephemeris['C_rc'] * cos2Phi_k
        di_k = ephemeris['C_is'] * sin2Phi_k + ephemeris['C_ic'] * cos2Phi_k

        u_k = Phi_k + du_k
        r_k = A * (1 - ephemeris['e'] * np.cos(E_k)) + dr_k
        i_k = ephemeris['i_0'] + di_k + ephemeris['IDOT'] * sv_position['GPS time']

        x_k_prime = r_k * np.cos(u_k)
        y_k_prime = r_k * np.sin(u_k)

        Omega_k = ephemeris['Omega_0'] + (ephemeris['OmegaDot'] - Earth_angular_velocity) * sv_position['GPS time'] - Earth_angular_velocity * ephemeris['t_oe']

        sv_position['Sat.X'] = x_k_prime * np.cos(Omega_k) - y_k_prime * np.cos(i_k) * np.sin(Omega_k)
        sv_position['Sat.Y'] = x_k_prime * np.sin(Omega_k) + y_k_prime * np.cos(i_k) * np.cos(Omega_k)
        sv_position['Sat.Z'] = y_k_prime * np.sin(i_k)
        return sv_position
    
    def residuals(self, x, xs, measured_pseudorange):
        r = np.linalg.norm(xs - x[:3], axis=1)
        return measured_pseudorange - (r + x[3])
    
    def least_squares(self, xs, measured_pseudorange, x0, b0):
        x_initial = np.hstack((x0, b0))
        result = least_squares(self.residuals, x_initial, args=(xs, measured_pseudorange))
        x_final = result.x
        return x_final[:3], x_final[3], result.cost

    def create_kml_file(self, coords, output_file):
        kml = simplekml.Kml()
        for coord in coords:
            lat, lon, alt = coord
            kml.newpoint(name="", coords=[(lon, lat, alt)])
        kml.save(output_file)

    def detect_spoofing(self, sv_position):
        # - Pseudorange measurements that deviate significantly from the median
        # - Satellites with unusually low or high C/N0 values
        pseudorange_median = sv_position['pseudorange'].median()
        deviation_threshold = 1e7
        cn0_threshold_low = 20
        cn0_threshold_high = 55  
        
        spoofed_sats = sv_position[
            (abs(sv_position['pseudorange'] - pseudorange_median) > deviation_threshold) |
            (sv_position['cn0'] < cn0_threshold_low) |
            (sv_position['cn0'] > cn0_threshold_high)
        ]
        
        return spoofed_sats
