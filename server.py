import csv
import glob
import os
import subprocess
import tempfile
import warnings

from android_rinex import gnsslogger_to_rnx
from astropy.time import Time
from flask import Flask, request, jsonify
from gnss_lib_py.utils.ephemeris_downloader import load_ephemeris
from gnss_lib_py.utils.constants import CONSTELLATION_ANDROID, CONSTELLATION_CHARS
import pandas as pd

# Suppress all warnings
warnings.filterwarnings("ignore")
app = Flask(__name__)

def convert(s):
    return s[0].upper() + s[1:]

def calc_position(df):
    median = df.median()
    answer = {}
    answer['lat'] = round(median['lat'], 5)
    answer['lon'] = round(median['lon'], 5)
    answer['alt'] = round(median['alt'])
    return answer

fields = ['svid', 'codeType', 'timeNanos', 'biasNanos', 'constellationType', 'svid', 
          'accumulatedDeltaRangeState', 'receivedSvTimeNanos', 'pseudorangeRateUncertaintyMetersPerSecond', 
          'accumulatedDeltaRangeMeters', 'accumulatedDeltaRangeUncertaintyMeters', 'carrierFrequencyHz', 
          'receivedSvTimeUncertaintyNanos', 'cn0DbHz', 'fullBiasNanos', 'multipathIndicator', 'timeOffsetNanos', 'state', 'pseudorangeRateMetersPerSecond']
converted_fields = ['Raw'] + list(map(convert, fields))

SOL_FIELDS = ['week', 'sec', 'lat', 'lon', 'alt', 'Q', 'ns', 'sdn(m)', 'sde(m)', 'sdu(m)', 'sdne(m)', 'sdeu(m)', 'sdun(m)', 'age(s)', 'ratio']
DEFAULT_EPHEM_PATH = os.path.join(os.getcwd(), 'data', 'ephemeris')

# Global variables to store the latest data
latest_measurement = None
latest_position = None
all_positions = {}

@app.route('/latest_data', methods=['GET'])
def latest_data():
    return jsonify({
        "measurement": latest_measurement,
        "position": latest_position,
        "all_positions": all_positions
    })

@app.route('/gnssdata', methods=['POST'])
def receive_gnss_data():
    global latest_measurement, latest_position, all_positions
    measurements = request.get_json()
    print("Received GNSS measurements:", measurements)
    
    if not measurements:
        return jsonify({"status": "failure", "error": "No measurements received"}), 400
    
    latest_measurement = measurements[-1] if measurements else None

    fd, SCRATCH = tempfile.mkstemp()
    os.close(fd)
    with open(SCRATCH, 'w', newline='') as csvfile:
        print('# ', file=csvfile)
        print('# Header Description:', file=csvfile)
        print('# ', file=csvfile)
        print('# Version: v9.9.9.9 Platform: 99 Manufacturer: A Model: a9999 GNSS Hardware Model Name: qcom;M9', file=csvfile)
        print('# ', file=csvfile)
        print('# ', file=csvfile, end='')
        writer = csv.DictWriter(csvfile, fieldnames=converted_fields)
        writer.writeheader()
        print('# ', file=csvfile)
        for measurement in measurements:
            filtered_measurement = {convert(key): measurement.get(key, None) for key in fields}
            filtered_measurement['Raw'] = 'Raw'
            writer.writerow(filtered_measurement)
    gnsslogger_to_rnx.convert(SCRATCH, SCRATCH + '.obs')
    os.remove(SCRATCH)
    
    file_paths = glob.glob(DEFAULT_EPHEM_PATH + '/**/*.rnx', recursive=True)
    paths_total = set(file_paths)
    paths_useful = set()
    for measurement in measurements:
        GpsTimeNanos = measurement['timeNanos'] - (measurement['fullBiasNanos'] - measurement['biasNanos'])
        gps_millis = GpsTimeNanos / 1e6
        # GLONASS has shift by 3 hours + 18 leap seconds, so just to be sure
        pathsBefore = load_ephemeris('rinex_nav', gps_millis - 6 * 60 * 60 * 1000, file_paths=paths_total)
        paths_total.update(pathsBefore)
        paths_useful.update(pathsBefore)
        pathsAfter = load_ephemeris('rinex_nav', gps_millis + 6 * 60 * 60 * 1000, file_paths=paths_total)
        paths_total.update(pathsAfter)
        paths_useful.update(pathsAfter)   
    
    subprocess.run(['rnx2rtkp', SCRATCH + '.obs', *paths_useful, '-p', '0', '-o', SCRATCH + '.sol'])
    result = pd.read_csv(SCRATCH + '.sol', comment='%', sep="\\s+", header=None, names=SOL_FIELDS)

    if result.empty:
        print('Error: rnx2rtkp did not like the data for some reason')
        return jsonify({"status": "failure", "error": "rnx2rtkp did not like the data for some reason"}), 400
    latest_position = calc_position(result)
    
    fromNameToLetter = {v: k for k, v in CONSTELLATION_CHARS.items()}
    fromNumberToName = CONSTELLATION_ANDROID
    constellationType = set()
    constellationName = set()
    for measurement in measurements:
        constellationType.add(fromNameToLetter[fromNumberToName[measurement['constellationType']]])
        constellationName.add(fromNumberToName[measurement['constellationType']])
    all_positions['+'.join(sorted(constellationName))] = latest_position
    for constellation in constellationType:
        subprocess.run(['rnx2rtkp', SCRATCH + '.obs', *paths_total, '-p', '0', '-o', SCRATCH + '.sol', '-sys', constellation])
        result = pd.read_csv(SCRATCH + '.sol', comment='%', sep="\\s+", header=None, names = ['week', 'sec', 'lat', 'lon', 'alt', 'Q', 'ns', 'sdn(m)', 'sde(m)', 'sdu(m)', 'sdne(m)', 'sdeu(m)', 'sdun(m)', 'age(s)', 'ratio'])
        if not result.empty:
            all_positions[CONSTELLATION_CHARS[constellation]] = calc_position(result)

    os.remove(SCRATCH + '.obs')
    os.remove(SCRATCH + '.sol')

    print(all_positions)
    return jsonify({
        "status": "success",
        "position": latest_position
    }), 200

if __name__ == '__main__':
    # pre-heat
    gps_millis = Time.now().gps * 1000

    file_paths = glob.glob(DEFAULT_EPHEM_PATH + '/**/*.rnx', recursive=True)
    paths_total = set(file_paths)
    pathsBefore = load_ephemeris('rinex_nav', gps_millis - 6 * 60 * 60 * 1000, file_paths=paths_total)
    paths_total.update(pathsBefore)
    pathsAfter = load_ephemeris('rinex_nav', gps_millis + 6 * 60 * 60 * 1000, file_paths=paths_total)

    app.run(host='0.0.0.0', port=2121)
