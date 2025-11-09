import os
from datetime import datetime, timezone
import georinex
import pandas as pd
import numpy as np
from gnss_lib_py.utils.ephemeris_downloader import load_ephemeris
from gnss_lib_py.utils.constants import CONSTELLATION_CHARS
from astropy.time import Time


class EphemerisManager():
    def __init__(self, data_directory=os.path.join(os.getcwd(), 'data', 'ephemeris')):
        self.data_directory = data_directory
        nasa_dir = os.path.join(data_directory, 'nasa')
        igs_dir = os.path.join(data_directory, 'igs')
        os.makedirs(nasa_dir, exist_ok=True)
        os.makedirs(igs_dir, exist_ok=True)
        self.data = None
        self.leapseconds = None

    def get_ephemeris(self, timestamp, satellites):
        systems = EphemerisManager.get_constellations(satellites)
        if not isinstance(self.data, pd.DataFrame):
            self.load_data(timestamp, systems)
        data = self.data
        if satellites:
            data = data.loc[data['sv'].isin(satellites)]
        data = data.loc[data['time'] < timestamp]
        data = data.sort_values('time').groupby(
            'sv').last()
        data = data.iloc[:, 1:]
        data['Leap Seconds'] = self.leapseconds
        return data

    def get_leapseconds(self, timestamp):
        return self.leapseconds

    def load_data(self, timestamp, constellations=None):
        data_list = []
        constellations_converted = None
        if constellations:
            constellations_converted = [CONSTELLATION_CHARS[constellation] for constellation in constellations]
        files = load_ephemeris(file_type="rinex_nav", gps_millis=Time(timestamp).gps * 1000, constellations=constellations_converted, download_directory="ephemeris_data")
        for file in files:
            data_list.append(self.read_ephemeris(file, constellations=constellations))
        if data_list:
            data = pd.concat(data_list, ignore_index=True)
        else:
            data = pd.DataFrame()

        data.reset_index(drop=True, inplace=True)
        data.sort_values('time', inplace=True, ignore_index=True)
        self.data = data
        
        
    def read_ephemeris(self, decompressed_filename, constellations=None):
        if not self.leapseconds:
            self.leapseconds = EphemerisManager.load_leapseconds(
                decompressed_filename)
        if constellations:
            data = georinex.load(decompressed_filename,
                                 use=constellations).to_dataframe()
        else:
            data = georinex.load(decompressed_filename).to_dataframe()
        data.dropna(how='all', inplace=True)
        data.reset_index(inplace=True)
        data['source'] = decompressed_filename
        WEEKSEC = 604800
        data['t_oc'] = pd.to_numeric(data['time'] - datetime(1980, 1, 6, 0, 0, 0))
        data['t_oc']  = 1e-9 * data['t_oc'] - WEEKSEC * np.floor(1e-9 * data['t_oc'] / WEEKSEC)
        data['time'] = data['time'].dt.tz_localize('UTC')
        data.rename(columns={'M0': 'M_0', 'Eccentricity': 'e', 'Toe': 't_oe', 'DeltaN': 'deltaN', 'Cuc': 'C_uc', 'Cus': 'C_us',
                             'Cic': 'C_ic', 'Crc': 'C_rc', 'Cis': 'C_is', 'Crs': 'C_rs', 'Io': 'i_0', 'Omega0': 'Omega_0'}, inplace=True)
        return data

    @staticmethod
    def load_leapseconds(filename):
        with open(filename) as f:
            for line in f:
                if 'LEAP SECONDS' in line:
                    return int(line.split()[0])
                if 'END OF HEADER' in line:
                    return None

    @staticmethod
    def get_constellations(satellites):
        if type(satellites) is list:
            systems = set()
            for sat in satellites:
                systems.add(sat[0])
            return systems
        else:
            return None

    @staticmethod
    def calculate_toc(timestamp):
        pass

if __name__ == '__main__':
    repo = EphemerisManager()
    target_time = datetime(2025, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
    repo.load_data(target_time)
    data = repo.get_ephemeris(target_time, ['G01', 'G03'])