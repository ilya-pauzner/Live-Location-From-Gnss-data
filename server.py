from flask import Flask, request, jsonify
import csv
import os
from datetime import datetime
from Parser import Parser
from ephemeris_manager import EphemerisManager
import navpy
import numpy as np
import warnings

# Suppress all warnings
warnings.filterwarnings("ignore")
app = Flask(__name__)

data_directory = os.path.join(os.getcwd(), 'data')
if not os.path.exists(data_directory):
    os.makedirs(data_directory)

data_file = os.path.join(data_directory, 'gnss_data.csv')
fields = ['svid', 'codeType', 'timeNanos', 'biasNanos', 'constellationType', 'svid', 
          'accumulatedDeltaRangeState', 'receivedSvTimeNanos', 'pseudorangeRateUncertaintyMetersPerSecond', 
          'accumulatedDeltaRangeMeters', 'accumulatedDeltaRangeUncertaintyMeters', 'carrierFrequencyHz', 
          'receivedSvTimeUncertaintyNanos', 'cn0DbHz', 'fullBiasNanos', 'multipathIndicator', 'timeOffsetNanos', 'state', 'pseudorangeRateMetersPerSecond']

# Global variables to store the latest data
latest_measurement = None
latest_position = None
latest_spoofed_sats = None
all_positions = None

# very much not ideal, (e.g. race conditions), but reading every time anew takes too long
ephemerisManager = None

@app.route('/latest_data', methods=['GET'])
def latest_data():
    return jsonify({
        "measurement": latest_measurement,
        "position": latest_position,
        "all_positions": all_positions,
        "spoofed_satellites": latest_spoofed_sats
    })

@app.route('/gnssdata', methods=['POST'])
def receive_gnss_data():
    global latest_measurement, latest_position, latest_spoofed_sats, all_positions
    measurements = request.get_json()
    print("Received GNSS measurements:", measurements)
    
    if not measurements:
        return jsonify({"status": "failure", "error": "No measurements received"}), 400
    
    latest_measurement = measurements[-1] if measurements else None

    with open(data_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fields)
        writer.writeheader()
        for measurement in measurements:
            filtered_measurement = {key: measurement.get(key, None) for key in fields}
            writer.writerow(filtered_measurement)
    
    parser = Parser(data_directory)
    parser.manager = ephemerisManager

    measurements = parser.open_file(data_file)
    measurements = parser.formatDF(measurements)
    
    if measurements.empty:
        print("Error: No valid measurements after formatting")
        return jsonify({"status": "failure", "error": "No valid measurements after formatting"}), 400
    
    results = {}
    positions = []
    constellations = measurements['constellationType'].unique()

    for constellation in constellations:
        one_epoch, ephemeris = parser.generate_epoch(measurements[measurements['constellationType'] == constellation])
        
        if one_epoch.empty or ephemeris.empty:
            print(f"Error: No valid epoch or ephemeris data for constellation {constellation}")
            continue
        
        sv_position = parser.calculate_satellite_position(ephemeris, one_epoch['transmit_time_seconds'])
        
        if sv_position.empty:
            print(f"Error: No valid satellite position data for constellation {constellation}")
            continue
        
        sv_position["pseudorange"] = one_epoch["Pseudorange_Measurement"] + parser.LIGHTSPEED * sv_position['Sat.bias']
        sv_position["cn0"] = one_epoch["Cn0DbHz"]
        sv_position = sv_position.drop('Sat.bias', axis=1)

        spoofed_sats = parser.detect_spoofing(sv_position)
        non_spoofed_svs = sv_position #.drop(spoofed_sats)

        if len(non_spoofed_svs) < 4:
            print(f"Error: Not enough satellites to calculate position for constellation {constellation} after excluding spoofed satellites")
            continue

        xs = non_spoofed_svs[['Sat.X', 'Sat.Y', 'Sat.Z']].to_numpy()
        pr = non_spoofed_svs['pseudorange'].to_numpy()
        x0 = np.array([0, 0, 0])
        b0 = 0
        try:
            x, b, _ = parser.least_squares(xs, pr, x0, b0)
            lla = navpy.ecef2lla(x)
            results[constellation] = {
                "position": lla,
                "spoofed_satellites": spoofed_sats.index.tolist()
            }
            positions.append(lla)
            if all_positions is None:
                all_positions = {}
            all_positions[constellation] = lla
            print('!!!', constellation, lla)
        except np.linalg.LinAlgError:
            print(f"Singular matrix encountered for constellation {constellation}. Skipping this calculation.")
            continue
        except Exception as e:
            print(f"An error occurred for constellation {constellation}: {e}")
            continue

    if not positions:
        return jsonify({"status": "failure", "error": "No valid position calculations"}), 400

    avg_position = np.mean(positions, axis=0)
    best_constellation = min(results.keys(), key=lambda k: np.linalg.norm(np.array(results[k]["position"]) - avg_position))

    latest_position = results[best_constellation]["position"]
    latest_spoofed_sats = results[best_constellation]["spoofed_satellites"]

    return jsonify({
        "status": "success",
        "position": latest_position,
        "spoofed_satellites": latest_spoofed_sats,
        "best_constellation": best_constellation
    }), 200

@app.route('/gnssnavdata', methods=['POST'])
def receive_gnss_navdata():
    message = request.get_json()
    print("Received GNSS navigation message:", message)
    # Process the navigation message as needed
    return jsonify({"status": "success"}), 200

if __name__ == '__main__':
    # pre-heat
    ephemerisManager = EphemerisManager(data_directory)
    ephemerisManager.load_data(datetime.now())

    app.run(host='0.0.0.0', port=2121)
