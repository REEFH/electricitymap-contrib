#!/usr/bin/env python3

import arrow
import json
import logging
import os
import math

import pandas as pd
import requests
import xml.etree.ElementTree as ET

API_ENDPOINT = 'https://opendata.reseaux-energies.fr/api/records/1.0/search/'

MAP_GENERATION = {
    'nucleaire': 'nuclear',
    'charbon': 'coal',
    'gaz': 'gas',
    'fioul': 'oil',
    'eolien': 'wind',
    'solaire': 'solar',
    'bioenergies': 'biomass'
}

MAP_HYDRO = [
    'hydraulique_fil_eau_eclusee',
    'hydraulique_lacs',
    'hydraulique_step_turbinage',
    'pompage'
]

def is_not_nan_and_truthy(v):
    if isinstance(v, float) and math.isnan(v):
        return False
    return bool(v)


def fetch_production(zone_key='FR', session=None, target_datetime=None,
                     logger=logging.getLogger(__name__)):
    if target_datetime:
        to = arrow.get(target_datetime, 'Europe/Paris')
    else:
        to = arrow.now(tz='Europe/Paris')

    # setup request
    r = session or requests.session()
    formatted_from = to.shift(days=-1).format('YYYY-MM-DDTHH:mm')
    formatted_to = to.format('YYYY-MM-DDTHH:mm')

    params = {
        'dataset': 'eco2mix-national-tr',
        'q': 'date_heure >= {} AND date_heure <= {}'.format(
            formatted_from, formatted_to),
        'timezone': 'Europe/Paris',
        'rows': 100
    }

    if 'RESEAUX_ENERGIES_TOKEN' not in os.environ:
        raise Exception(
            'No RESEAUX_ENERGIES_TOKEN found! Please add it into secrets.env!')
    params['apikey'] = os.environ['RESEAUX_ENERGIES_TOKEN']

    # make request and create dataframe with response
    response = r.get(API_ENDPOINT, params=params)
    data = json.loads(response.content)
    data = [d['fields'] for d in data['records']]
    df = pd.DataFrame(data)

    # filter out desired columns and convert values to float
    value_columns = list(MAP_GENERATION.keys()) + MAP_HYDRO
    df = df[['date_heure'] + value_columns]
    df[value_columns] = df[value_columns].astype(float)

    datapoints = list()
    for row in df.iterrows():
        production = dict()
        for key, value in MAP_GENERATION.items():
            production[value] = row[1][key]

        # Hydro is a special case!
        production['hydro'] = row[1]['hydraulique_lacs'] + row[1]['hydraulique_fil_eau_eclusee']
        storage = {
            'hydro': row[1]['pompage'] * -1 + row[1]['hydraulique_step_turbinage'] * -1
        }

        # if all production values are null, ignore datapoint
        if not any([is_not_nan_and_truthy(v)
                    for k, v in production.items()]):
            continue

        datapoints.append({
            'zoneKey': zone_key,
            'datetime': arrow.get(row[1]['date_heure']).datetime,
            'production': production,
            'storage': storage,
            'source': 'opendata.reseaux-energies.fr'
        })

    return datapoints


def fetch_price(zone_key, session=None, target_datetime=None,
                logger=logging.getLogger(__name__)):
    if target_datetime:
        now = arrow.get(target_datetime, tz='Europe/Paris')
    else:
        now = arrow.now(tz='Europe/Paris')

    r = session or requests.session()
    formatted_from = now.shift(days=-1).format('DD/MM/YYYY')
    formatted_to = now.format('DD/MM/YYYY')

    url = 'http://www.rte-france.com/getEco2MixXml.php?type=donneesMarche&da' \
          'teDeb={}&dateFin={}&mode=NORM'.format(formatted_from, formatted_to)
    response = r.get(url)
    obj = ET.fromstring(response.content)
    datas = {}

    for donnesMarche in obj:
        if donnesMarche.tag != 'donneesMarche':
            continue

        start_date = arrow.get(arrow.get(donnesMarche.attrib['date']).datetime, 'Europe/Paris')

        for item in donnesMarche:
            if item.get('granularite') != 'Global':
                continue
            country_c = item.get('perimetre')
            if zone_key != country_c:
                continue
            value = None
            for value in item:
                if value.text == 'ND':
                    continue
                period = int(value.attrib['periode'])
                datetime = start_date.replace(hours=+period).datetime
                if not datetime in datas:
                    datas[datetime] = {
                        'zoneKey': zone_key,
                        'currency': 'EUR',
                        'datetime': datetime,
                        'source': 'rte-france.com',
                    }
                data = datas[datetime]
                data['price'] = float(value.text)

    return list(datas.values())


if __name__ == '__main__':
    print(fetch_production())
