import datetime
import calendar

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import and_, or_
from sqlalchemy.sql import func
from sqlalchemy import case
from sqlalchemy import distinct

import numpy as np
import pandas as pd
import pgpasslib

from pycds import *

class WorkflowTools:
    """The work flow tool helps query the Meteorlogical 
    Services Canada database at PCIC using ORM to return
    design values used in the national building code project.
    
    Attributes:
        start_time (datetime object): take all observations after
        end_time (datetime object): take all observations before
        month (int, optional): month to extract design values
            where necessary
    """

    def __init__(self, start_time, end_time, month=1, days_in_month=31):

        self.month = month
        self.start_time = start_time
        self.end_time = end_time

        # query variables

        # year range 
        self.yr_interval = float(np.abs(self.end_time.year-self.start_time.year))
        
        # total number of days in month
        self.total_days = days_in_month*self.yr_interval
       
        # total number of hours in month
        self.total_hours = self.total_days*24

        # record the max and min time taken from the database
        self.time_max = func.max(Obs.time).label("time_max")
        self.time_min = func.min(Obs.time).label("time_min")

        print(self.yr_interval, self.total_days, self.total_hours)

        # record lat/lon/station_id from history of the station
        self.lat = History.lat.label("lat")
        self.lon = History.lon.label("lon")
        self.station_id = History.station_id.label("station_id")

        # count the observations used in calculation percentile
        self.count = func.count(Obs.datum).label('obs_count')

        # create a condition that separates daily and hourly data and calculates
        # the completeness based on total number of theoretical observations
        self.daily_complete = (self.count/self.total_days).label('completeness')
        self.hourly_complete = (self.count/self.total_hours).label('completeness')
        self.annual_complete = (self.count/365.0).label('completeness')
        # check start and end times
        if (start_time < end_time) is False:
            raise ValueError("Start time cannot be later than end time.")

    def safe_month(self, month=None):
        if month is None:
            month = self.month

        if (month > 12) or (month < 1):
            raise ValueError("Invalid month specified.")

        return month

    def query_annual_rain(self, session):
        """A query to get the total annual average rainfall amount 
        at a given station over a range of years. The table contains
        the average total rainfall in mm, the maximum and minimum time used to
        calculate the average from the station, the latitude, longitude
        and the station id. This method uses daily total rainfall.
        -----------------------------------------
        Args:
            session (sqlalchemy Session): session constructed using
                connection string and engine
        Returns: 
            query (sqlalchemy query): sqlalchemy query object 
                for annual average rainfall
        """
        yr_interval = float(np.abs(self.end_time.year-self.start_time.year))

        if (yr_interval) < 1.0:
            raise ValueError("Annual rainfall value requires \
                             a time window of at least one year.")
            return None

        annual_rain = func.sum(Obs.datum*0.1/self.yr_interval).label("annual_rain")

        # construct desired table
        query = (
                 session.query(annual_rain,
                               self.time_min,
                               self.time_max,
                               self.lat,
                               self.lon,
                               self.station_id,
                               self.count,
                               self.annual_complete)
                        .select_from(Obs)
                        .join(Variable, Obs.vars_id == Variable.id)
                        .join(History, Obs.history_id == History.id)
                        .filter(and_(Obs.time >= self.start_time,
                                	 Obs.time < self.end_time))
                        .filter(and_(Variable.standard_name == 'thickness_of_rainfall_amount',
                                     Variable.cell_method == 'time: sum'))
                        .filter(or_(Variable.name == '10',
                        			Variable.name == '48'))
                        .group_by(History.lat,
                                  History.lon,
                                  History.station_id)
                 )

        return query

    def query_annual_precip(self, session, month=None):
        """A query to get the total annual average precipitation amount 
        at a given station over a range of years. The table contains
        the average total rainfall in mm, the maximum and minimum time used to
        calculate the average from the station, the completeness of 
        calculated value the latitude, longitude and the station id.  
        -----------------------------------------
        Args:
            session (sqlalchemy Session): session constructed using
                connection string and engine
        Returns: 
            query (sqlalchemy query): sqlalchemy query object 
                for annual average precip
        """
        if (self.yr_interval) < 1.0:
            raise ValueError("Annual precipitation value requires \
                             a time window of at least one year.")
            return None

        annual_precip = func.sum(Obs.datum*0.1/self.yr_interval).label("annual_precip")
    
        query = (
                 session.query(annual_precip,
                               self.time_min,
                               self.time_max,
                               self.lat,
                               self.lon,
                               self.station_id,
                               self.count,
                               self.annual_complete)
                        .select_from(Obs)
                        .join(Variable, Obs.vars_id == Variable.id)
                        .join(History, Obs.history_id == History.id)
                        .filter(and_(Obs.time >= self.start_time,
                                	 Obs.time < self.end_time))
                        .filter(and_(Variable.standard_name 
                                == 'lwe_thickness_of_precipitation_amount',
                                     Variable.cell_method == 'time: sum'))
                        .filter(or_(Variable.name == '12',
                        			Variable.name == '50'))
                        .group_by(History.lat,
                                  History.lon,
                                  History.station_id)
                 )

        return query
    
    def query_design_temp_percentile(self, session, percentile=0.01, days_in_month=31, month=1):
        """A query to get the 1st percentile of a given month across
        the entire operating history of a station in a range of time. 
        Only the year from start and end times are used to create the
        time frame. All daily minimum air temperatures are used
        -----------------------------------------
        Args:
            session (sqlalchemy Session): session constructed using
                connection string and engine
            percentile (float): desire percentile in fraction
        Returns: 
            query (sqlalchemy query): sqlalchemy query constructed 
                using ORM to query temperature percentiles 
        """

        month = self.safe_month(month)

        p = (
             (func.percentile_cont(percentile)
                 .within_group(Obs.datum.asc())*0.1)
                 .label("air_temperature")   
             )

        query = (
                 session.query(p,
                               self.time_min,
                               self.time_max,
                               self.lat,
                               self.lon,
                               self.station_id,
                               self.daily_complete
                               )
                        .select_from(Obs)
                        .join(Variable, Obs.vars_id == Variable.id)
                        .join(History, Obs.history_id == History.id)
                        .filter(and_(Obs.time >= self.start_time,
                                     Obs.time < self.end_time))
                        .filter(func.extract("month", Obs.time) == month)
                        .filter(Variable.name == '2')
                        .filter(and_(Variable.standard_name == 'air_temperature',
                                     Variable.cell_method == 'time: minimum'))
                        .filter(Obs.datum != 0.0) # bad obs are sometimes 0.0
                        .group_by(History.lat, 
                                  History.lon, 
                                  History.station_id)
                 )
  
        return query

    def query_design_temp_dry(self, session, days_in_month=31, month=None, percentile=0.025):
        """A query to get the 2.5th percentile of July across
        the entire operating history of a station in a range of time. 
        Only the year from start and end times are used to create the
        time frame. All frequencies of observations are used, and the
        regular non-corrected air temperature is being used for this 
        calculation.
        -----------------------------------------
        Returns: 
            query (sqlalchemy query): sqlalchemy query constructed 
                using ORM to query temperature percentiles 
        """

        month = self.safe_month(month)

        # get percentile from group convert to celsius
        p = (
             (func.percentile_cont(percentile) 
                  .within_group(Obs.datum.asc())*0.1)
                  .label("wet_bulb_temperature")
             )

        # construct query table
        query = (
                 session.query(p, 
                               self.time_min,
                               self.time_max,
                               self.lat,
                               self.lon,
                               self.station_id,
                               self.daily_complete,
                               self.count)
                        .select_from(Obs)
                        .join(Variable, Obs.vars_id == Variable.id)
                        .join(History, Obs.history_id == History.id)
                        .filter(and_(Obs.time >= self.start_time,
                                     Obs.time < self.end_time))
                        .filter(func.extract("month", Obs.time) == month)
                        .filter(and_(Variable.standard_name == 'air_temperature', 
                                     Variable.cell_method == 'time: maximum'))
                        .filter(Variable.name == '1')                  
                        .filter(Obs.datum != 0.0)   # bad obs are sometimes 0.0
                        .group_by(History.lat, 
                                  History.lon, 
                                  History.station_id)
                 )

        return query

    def query_design_temp_wet(self, session, days_in_month=31, month=None, percentile=0.025):
        """A query to get the 1st percentile of a given month across
        the entire operating history of a station in a range of time. 
        Only the year from start and end times are used to create the
        time frame. All frequencies of observations are used, and the
        regular non-corrected air temperature is being used for this 
        calculation.
        -----------------------------------------
        Returns: 
            query (sqlalchemy query): sqlalchemy query constructed 
                using ORM to query temperature percentiles 
        """
        month = self.safe_month(month)

        # create a condition that separates daily and hourly data and calculates
        # the completeness based on total number of theoretical observations
        expr = (
                case([(func.count(Obs.datum) <= 31,
                       func.count(Obs.datum)/self.total_days)], 
                       else_=func.count(Obs.datum)/self.total_hours)
                               .label('completeness')
                )
        
        # get percentile from group convert to celsius
        p = (
             (func.percentile_cont(percentile) 
                  .within_group(Obs.datum.asc())*0.1)
                  .label("wet_bulb_temp")
             )

        query = (
        		 session.query(p,
                               self.time_min,
                               self.time_max,
                               self.lat,
                               self.lon,
                               self.station_id,
                               expr,
                               self.count)
                        .select_from(Obs)
                        .join(Variable, Obs.vars_id == Variable.id)
                        .join(History, Obs.history_id == History.id)
                        .filter(and_(Obs.time >= self.start_time,
                                     Obs.time < self.end_time))
                        .filter(func.extract("month", Obs.time) == month)
                        .filter(Variable.name == '79')
                        .filter(Variable.standard_name == 'wet_bulb_temperature')
                        .filter(Obs.datum != 0.0) # bad obs are sometimes 0.0
                        .group_by(History.lat, 
                                  History.lon, 
                                  History.station_id)
                 )

        return query

    def query_variables(self, session):
        """A simple query to get all available variables to 
        reference while building workflow tool. 
        -----------------------------------------
        Returns: 
            query (sqlalchemy query): sqlalchemy query constructed 
                using ORM to query variables
        """

        query = session.query(Variable.id,
                              Variable.standard_name,
                              Variable.description,
                              Variable.unit,
                              Variable.cell_method,
                              Variable.name)

        return query

    def query_deg_day_below_18(self, session):
        """A query to get the heating degree days (hdd) 
        "Degree Days Below 18C". If start/end time
        range is longer than a year, then the average
        degree day across the annual range is used.
        Each comparison is made between 18C and a 
        daily mean temperature.
        -----------------------------------------
        Returns: 
            query (sqlalchemy query): sqlalchemy query object
            containing hdd values   
        """
        # get heating degree days below 18 C, convert to celsius, take mean
        hdd = func.sum((18.0-Obs.datum*0.1)/self.yr_interval).label("hdd")

        query = (
                 session.query(hdd,
                               self.time_min,
                               self.time_max,
                               self.lat, 
                               self.lon,
                               self.station_id,
                               self.count/365.0
                               )
                        .select_from(Obs)
                        .join(Variable, Obs.vars_id == Variable.id)
                        .join(History, Obs.history_id == History.id)
                        .filter(and_(Obs.time >= self.start_time,
                                     Obs.time < self.end_time))
                        .filter(func.extract("month", Obs.time) == month)
                        .filter(Variable.name == '3')
                        .filter(and_(Variable.standard_name == 'air_temperature',
                                     Variable.cell_method == 'time: mean'))
                        .filter(Obs.datum != 0.0) # bad obs are sometimes 0.0
                        .group_by(History.lat, 
                                  History.lon, 
                                  History.station_id)
                )
  
        return query
