from flask import Flask, request, render_template
import geopy.distance
from airports_coordinates import airports_coordinates


def get_coordinates(airport_code):
    return airports_coordinates.get(airport_code)


app = Flask(__name__)


@app.route("/")
def index():
        return render_template("index.html")



@app.route("/calculate", methods=["POST"])
def calculate():
    dep_airport = request.form["airport1"].upper()
    arr_airport = request.form["airport2"].upper()
    speed = float(request.form["speed"])

    if dep_airport not in airports_coordinates:
        return "Airport 1 with code " + dep_airport + " not found in our database."
    if arr_airport not in airports_coordinates:
        return "Airport 2 with code " + arr_airport + " not found in our database."

    # great circle distance
    distance = geopy.distance.great_circle(get_coordinates(dep_airport), get_coordinates(arr_airport)).nm
    # flight time added for departure and arrival time
    flight_time = distance / speed
    if flight_time > 0.4:
        flight_time += 0.333
    else:
        flight_time += 0.20
    hours = int(flight_time)
    minutes = int((flight_time - hours) * 60)
    result = f"{hours} hours {minutes} minutes"
    return render_template("index.html", result=result)


@app.route("/findplane", methods=["POST"])
def findplane():
    aircraft1_pos = request.form["aircraft1_pos"].upper()
    aircraft2_pos = request.form["aircraft2_pos"].upper()
    dep_airport = request.form["airport1h"].upper()
    speed = 404

    #check validity of inputs

    if aircraft1_pos not in airports_coordinates:
        return "Airport  with code " + aircraft1_pos + " not found in our database."
    if aircraft2_pos not in airports_coordinates:
        return "Airport  with code " + aircraft2_pos + " not found in our database."
    if dep_airport not in airports_coordinates:
        return "Airport  with code " + dep_airport + " not found in our database."

    distance_1 = geopy.distance.great_circle(get_coordinates(aircraft1_pos), get_coordinates(dep_airport)).nm
    # flight time added for departure and arrival time
    flight_time_1 = distance_1 / speed
    if flight_time_1 > 0.4:
        flight_time_1 += 0.333
    else:
        flight_time_1 += 0.20

    distance_2 = geopy.distance.great_circle(get_coordinates(aircraft2_pos), get_coordinates(dep_airport)).nm
    # flight time added for departure and arrival time
    flight_time_2 = distance_2 / speed
    if flight_time_2 > 0.4:
        flight_time_2 += 0.333
    else:
        flight_time_2 += 0.20
    #choose which plane is closer to next flight
    if flight_time_1 == flight_time_2:
        findplaneresults = "Mozete odabrat bilo koji avion"
    if flight_time_1 > flight_time_2:
        findplaneresults = f"Choose aircraft 2, you will save", int((flight_time_1-flight_time_2)*60), "minutes"
    if flight_time_1 < flight_time_2:
        findplaneresults = f"choose aircraft 1, you will save", int((flight_time_2-flight_time_1)*60), "minutes"
    return render_template("index.html", findplaneresults=findplaneresults)




if __name__ == '__main__':
    app.run()
