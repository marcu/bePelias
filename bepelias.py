#!/usr/bin/env python
# coding: utf-8

"""
Flask part of bePelias geocoder

@author: Vandy Berten (vandy.berten@smals.be)

"""

# pylint: disable=line-too-long
# pylint: disable=invalid-name

import os
import logging
import sys
import json
import re

from urllib.parse import unquote_plus

from flask import Flask, url_for
from flask_restx import Api, Resource, reqparse, fields

from elasticsearch import Elasticsearch, NotFoundError

from utils import (log, vlog, get_arg,
                   build_address, to_rest_guidelines,
                   build_city, struct_or_unstruct, advanced_mode)

from pelias import Pelias, PeliasException


logging.basicConfig(format='[%(asctime)s]  %(message)s', stream=sys.stdout)


# WARNING : no logs
# INFO : a few logs
# DEBUG : lots of logs

logger = logging.getLogger()

env_log_level = os.getenv('LOG_LEVEL', "HIGH").upper().strip()
if env_log_level == "LOW":
    logger.setLevel(logging.WARNING)
elif env_log_level == "MEDIUM":
    logger.setLevel(logging.INFO)
elif env_log_level == "HIGH":
    logger.setLevel(logging.DEBUG)
else:
    print(f"Unkown log level '{env_log_level}'. Should be LOW/MEDIUM/HIGH")

logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("elasticsearch").setLevel(logging.WARNING)

env_pelias_host = os.getenv('PELIAS_HOST')
if env_pelias_host:
    logging.info("get PELIAS_HOST from env: %s", env_pelias_host)
    pelias_host = env_pelias_host
else:
    pelias_host = "10.0.2.15:4000"
    logging.info("Use default osm host: %s", pelias_host)

pelias = Pelias(domain=pelias_host)

log("test Pelias: ")
try:
    log(pelias.geocode("20, Avenue Fonsny, 1060 Bruxelles"))
except PeliasException as exc:
    log("Test failed!!")
    log(exc)


pelias.wait()

log("Waiting for requests...")


app = Flask(__name__)
api = Api(app,
          version='1.0.0',
          title='bePelias API',
          description="""A service that allows geocoding (postal address cleansing and conversion into geographical coordinates), based on Pelias and BestAddresses.

          Code available on https://github.com/SmalsResearch/bePelias/

          """,
          doc='/doc',
          prefix='/REST/bepelias/v1',
          contact='Vandy BERTEN',
          contact_email='vandy.berten@smals.be'
          )

namespace = api.namespace(
    '',
    'Main namespace')

with_https = os.getenv('HTTPS', "NO").upper().strip()

if with_https == "YES":
    # It runs behind a reverse proxy
    @property
    def specs_url(self) -> str:
        """
            If it runs behind a reverse proxy
        """
        return url_for(self.endpoint('specs'), _external=True, _scheme='https')

    Api.specs_url = specs_url

single_parser = reqparse.RequestParser()

single_parser.add_argument('mode',
                           type=str,
                           choices=('basic', 'simple', 'advanced'),
                           default='advanced',
                           help="""
How Pelias is used:

- basic: Just call the structured version of Pelias
- simple: Call the structured version of Pelias. If it does not get any result, call the unstructured version
- advanced: try several variants until it gives a result""")

single_parser.add_argument('streetName',
                           type=str,
                           default='Avenue Fonsny',
                           help="The name of a passage or way through from one location to another (cf. Fedvoc). Example: 'Avenue Fonsny'",
                           # example= "Avenue Fonsny"
                           )

single_parser.add_argument('houseNumber',
                           type=str,
                           default='20',
                           help="An official alphanumeric code assigned to building units, mooring places, stands or parcels (cf. Fedvoc). Example: '20'",
                           )

single_parser.add_argument('postCode',
                           type=str,
                           default='1060',
                           help="The post code (a.k.a postal code, zip code etc.) (cf. Fedvoc). Example: '1060'",
                           # example= "Avenue Fonsny"
                           )

single_parser.add_argument('postName',
                           type=str,
                           default='Saint-Gilles',
                           help="Name with which the geographical area that groups the addresses for postal purposes can be indicated, usually the city (cf. Fedvoc). Example: 'Bruxelles'",
                           )

single_parser.add_argument('withPeliasResult',
                           type=bool,
                           default=False,
                           help="If True, return Pelias result as such in 'peliasRaw'.",
                           )


city_search_parser = reqparse.RequestParser()
city_search_parser.add_argument('postCode',
                                type=str,
                                default='1060',
                                help="The post code (a.k.a postal code, zip code etc.) (cf. Fedvoc). Example: '1060'",
                                # example= "Avenue Fonsny"
                                )

city_search_parser.add_argument('postName',
                                type=str,
                                default='Saint-Gilles',
                                help="Name with which the geographical area that groups the addresses for postal purposes can be indicated, usually the city (cf. Fedvoc). Example: 'Bruxelles'",
                                )

id_parser = reqparse.RequestParser()
id_parser.add_argument('bestid',
                       type=str,
                       default='https%3A%2F%2Fdatabrussels.be%2Fid%2Faddress%2F219307%2F4',
                       help="BeSt Id for an address, a street or a municipality. Value has to be url encoded (i.e., replace '/' by '%2F', ':' by '%3A')",
                       location='query'
                       )

name_model = namespace.model("ItemNameModel", {
    "fr":  fields.String(example="Avenue Fonsny",
                         description="Entity (street, municipality...) name in French",
                         skip_none=True),
    "nl":  fields.String(example="Fonsnylaan",
                         description="Entity (street, municipality...) name in Nederlands",
                         skip_none=True),
    "de":  fields.String(example="Avenue Fonsny",
                         description="Entity (street, municipality...) name in German",
                         skip_none=True)})

street_model = namespace.model("StreetNameModel", {
    "name": fields.Nested(name_model,
                          description="Street name in fr/nl/de (when applicable)",
                          skip_none=True),
    "id": fields.String(example="https://databrussels.be/id/streetname/4921/2",
                        description="Street BeSt id",
                        skip_none=True)
}, skip_none=True)

municipality_model = namespace.model("MunicipalityModel", {
    "name": fields.Nested(name_model,
                          description="Municipality name in fr/nl/de (when applicable)",
                          skip_none=True),
    "code": fields.String(example="21013",
                          description="Municipality code or code NIS"),
    "id": fields.String(example="https://databrussels.be/id/municipality/21013/14",
                        description="Municipality BeSt id", skip_none=True)
})

part_of_municipality_model = namespace.model("PartOfMunicipalityModel", {
    "name": fields.Nested(name_model,
                          description="Part of Municipality name in fr/nl/de (when applicable)",
                          skip_none=True),
    "id": fields.String(example="geodata.wallonie.be/id/PartOfMunicipality/1415/1",
                        description="Part of Municipality BeSt id (only in Wallonia)", skip_none=True)
    },
    skip_none=True)


postalinfo_model = namespace.model("PostalInfoModel", {
    "name": fields.Nested(name_model,
                          description="PostalInfo in fr/nl/de (when applicable ; only in Brussels and Flanders)",
                          skip_none=True),
    "postalCode": fields.String(example="1060",
                                description="Postal code", skip_none=True),
})

coordinates_model = namespace.model("CoordinatesModel", {
    "lat": fields.Float(description="Latitude, in EPSG:4326. Angular distance from some specified circle or plane of reference",
                        example=50.8358677,
                        skip_none=True
                        ),
    "lon": fields.Float(description="Longitude, in EPSG:4326. Angular distance measured on a great circle of reference from the intersection " +
                                    "of the adopted zero meridian with this reference circle to the similar intersection of the meridian passing through the object",
                        example=4.3385087,
                        skip_none=True)
}, skip_none=True)

boxinfo_model = namespace.model("BoxinfoModel",
                                {
                                    "lat": fields.Float(description="Latitude, in EPSG:4326. Angular distance from some specified circle or plane of reference",
                                                        example=50.8358677),
                                    "lon": fields.Float(description="Longitude, in EPSG:4326. Angular distance measured on a great circle of reference from the intersection " +
                                                                    "of the adopted zero meridian with this reference circle to the similar intersection of the meridian passing through the object",
                                                        example=4.3385087),
                                    "boxNumber": fields.String(example="b001, A, ...",
                                                               description="Box number"),
                                    "addressId": fields.String(example="https://databrussels.be/id/address/219307/4",
                                                               description="Address BeSt id"),
                                    "status": fields.String(example="current/retired/proposed",
                                                            description="BeSt Address status"),
                                }, skip_none=True)

item_model = namespace.model("ItemModel", {
    "bestId": fields.String(example="https://databrussels.be/id/address/219307/4",
                            description="Address BeSt id (could be street or municipality?)"),
    "street": fields.Nested(street_model, description="Street info", skip_none=True),
    "municipality": fields.Nested(municipality_model, description="Municipality info", skip_none=True),
    "partOfMunicipality": fields.Nested(part_of_municipality_model, description="Part of Municipality info (only in Wallonia)", skip_none=True),
    "postalInfo": fields.Nested(postalinfo_model, description="Postal info", skip_none=True),
    "housenumber": fields.String(example="20, 20A, 20-22, ...",
                                 description="House number",
                                 skip_none=True),
    "status": fields.String(example="current/retired/proposed",
                            description="BeSt Address status",
                            skip_none=True),
    "precision": fields.String(example="address",
                               description="Level of precision. See README.md#precision"),
    "coordinates": fields.Nested(coordinates_model, description="Geographic coordinates (in EPSG:4326)", skip_none=True),
    "boxInfo":  fields.List(fields.Nested(boxinfo_model), skip_none=True),
    "name": fields.String(example="Bruxelles",
                          description="If we can't find any result from BeSt Address but get some approximate results from other sources",
                          skip_none=True),
}, skip_none=True)


city_item_model = namespace.model("CityItemModel", {
    "municipality": fields.Nested(municipality_model, description="Municipality info"),
    "partOfMunicipality": fields.Nested(part_of_municipality_model, description="Part of Municipality info", skip_none=True),
    "postalInfo": fields.Nested(postalinfo_model, description="Postal info", skip_none=True),
    "coordinates": fields.Nested(coordinates_model, description="Geographic coordinates (in EPSG:4326)"),
}, skip_none=True)


geocode_output_model = namespace.model("GeocodeOutput", {
    "items":  fields.List(fields.Nested(item_model, skip_none=True), skip_none=True),
    "peliasRaw": fields.Raw(default=None,
                            description="Result provided by underlying Pelias. Only with 'witPeliasResult:true",
                            skip_none=True),
    "callType": fields.String(default="struct", example="struct or unstruct"),
    "inAddr": fields.Raw(example={
                            "address": "Avenue Fonsny, 20",
                            "locality": "",
                            "postalcode": "1060"
                         },
                         description="Address sent to Pelias. Could be a dict (if callType='struct') or a string (if callType='unstruct')"),
    "peliasCallCount": fields.Integer(example=1,
                                      description="How many calls to Pelias were required to get this result"),
    "transformers": fields.String(example="clean;no_city",
                                  description="Which transformation methods were used before sending the address to Pelias"),
    }, skip_none=True)


search_city_output_model = namespace.model("SearchCityOutput", {
    "items":  fields.List(fields.Nested(city_item_model, skip_none=True), skip_none=True),
    }, skip_none=True)

get_by_id_output_model = namespace.model("GetByIdOutput", {
    "items":  fields.List(fields.Nested(item_model, skip_none=True), skip_none=True),
    }, skip_none=True)


@namespace.route('/geocode')
class Geocode(Resource):
    """ Single address geocoding"""

    @namespace.expect(single_parser)
    @namespace.response(400, 'Error in arguments')
    @namespace.response(500, 'Internal Server error')
    @namespace.response(204, 'No address found')
    @namespace.marshal_with(geocode_output_model,
                            description='Found one or several matches for this address',
                            skip_none=True)
    def get(self):
        """
Geocode (postal address cleansing and conversion into geographical coordinates) a single address.

        """

        log("geocode")

        mode = get_arg("mode", "advanced")
        if mode not in ["basic", "simple", "advanced", "pelias_struct", "pelias_struct_noloc", "pelias_unstruct"]:
            namespace.abort(400, f"Invalid mode {mode}")

        street_name = get_arg("streetName", None)
        house_number = get_arg("houseNumber", None)
        post_code = get_arg("postCode", None)
        post_name = get_arg("postName", None)

        withPeliasResult = get_arg("withPeliasResult", "False")
        if withPeliasResult.lower() == "false":
            withPeliasResult = False
        elif withPeliasResult.lower() == "true":
            withPeliasResult = True
        else:
            namespace.abort(400, f"Invalid withPeliasResult value ({mode}). Should be 'true' or 'false'")

        if street_name:
            street_name = street_name.strip()
        if house_number:
            house_number = house_number.strip()
        if post_code:
            post_code = post_code.strip()
        if post_name:
            post_name = post_name.strip()

        log(f"Request: {street_name} / {house_number} / {post_code} / {post_name} ")

        log(f"Mode: {mode}")

        try:

            if mode in ("basic", "pelias_struct"):
                pelias_res = pelias.geocode({"address": build_address(street_name, house_number),
                                             "postalcode": post_code,
                                             "locality": post_name})

                return to_rest_guidelines(pelias_res, withPeliasResult)

            if mode == "pelias_struct_noloc":
                pelias_res = pelias.geocode({"address": build_address(street_name, house_number),
                                            "postalcode": post_code})

                return to_rest_guidelines(pelias_res, withPeliasResult)

            if mode == "pelias_unstruct":
                addr = build_address(street_name, house_number) + ", " + build_city(post_code, post_name)
                pelias_res = pelias.geocode(addr)

                return to_rest_guidelines(pelias_res, withPeliasResult)

            if mode == "simple":
                pelias_res = struct_or_unstruct(street_name, house_number, post_code, post_name, pelias)
                return to_rest_guidelines(pelias_res, withPeliasResult)

            if mode == "advanced":
                vlog("advanced...")

                pelias_res = advanced_mode(street_name, house_number, post_code, post_name, pelias)
                return to_rest_guidelines(pelias_res, withPeliasResult)

        except PeliasException as exc:
            log("Exception during process: ")
            log(exc)
            return str(exc), 500

        return "Wrong mode!"  # Should neve occur...


@namespace.route('/searchCity')
class SearchCity(Resource):
    """ Search city level results"""

    @namespace.expect(city_search_parser)
    @namespace.response(400, 'Error in arguments')
    @namespace.response(500, 'Internal Server error')
    @namespace.response(204, 'No address found')
    @namespace.marshal_with(search_city_output_model,
                            description='Found one or several matches for city/postal code',
                            skip_none=True)
    def get(self):
        """
Search a city based on a postal code or a name (could be municipality name, part of municipality name or postal name)

        """
        log("search city")

        post_code = get_arg("postCode", None)
        post_name = get_arg("postName", None)

        must = [{"term": {"layer": "locality"}}]
        if post_code:
            must.append({"term": {"address_parts.zip": post_code}})
        if post_name:
            must.append({"query_string": {"query": f"name.default:\"{post_name}\""}})

        try:
            client = Elasticsearch(pelias.elastic_api)
            resp = client.search(index="pelias", body={
                "query": {
                    "bool": {
                        "must": must
                    }
                }
            })
            log("resp:")
            log(resp)

            resp = resp["hits"]["hits"]

            final_result = []
            for resp_item in resp:
                if "addendum" in resp_item["_source"] and "best" in resp_item["_source"]["addendum"]:
                    it = {"properties": {"addendum": {"best": json.loads(resp_item["_source"]["addendum"]["best"])}}}
                    if "center_point" in resp_item["_source"]:
                        it["geometry"] = {"coordinates": resp_item["_source"]["center_point"]}
                    it["name"] = resp_item["_source"]["name"]

                    final_result.append(it)
            # for i in range(len(resp)):
                # resp[i]["_source"]["addendum"]["best"] =json.loads(resp[i]["_source"]["addendum"]["best"])

            if len(final_result) == 0:
                return "Object not found", 204

            # final_result = list(unique_everseen(final_result))

            # Remove duplicate results
            final_result = {"features": [i for n, i in enumerate(final_result) if i not in final_result[:n]]}

            return to_rest_guidelines(final_result, False)
        except NotFoundError:
            pass
        except ConnectionError as exc:
            log("ES ConnectionError")
            log(exc)

            return f"Cannot connect to Elastic: {exc}", 500

        return "Object not found", 204


@namespace.route('/id/<string:bestid>')
# @namespace.route('/id/<string:bestid>/<string:a1>/<string:a2>/<string:a3>/<string:a4>/')
# @namespace.route('/id/<string:bestid>/<string:a1>/<string:a2>/<string:a3>/<string:a4>/<string:a5>/')
@namespace.param('bestid',
                 type=str,
                 default='https%3A%2F%2Fdatabrussels.be%2Fid%2Faddress%2F219307%2F4',
                 help="BeSt Id for an address, a street or a municipality. Value has to be url encoded (i.e., replace '/' by '%2F', ':' by '%3A')")
class GetById(Resource):
    """ Get ressource by best id. This does not replace a call to Bosa BeSt Address API !!
    Only works for addresses, streets and municipalities (not for postalinfos, part of municipalities)
    """

    # @namespace.expect(id_parser)

    @namespace.response(400, 'Error in arguments')
    @namespace.response(500, 'Internal Server error')
    @namespace.response(204, 'No address found')
    @namespace.marshal_with(get_by_id_output_model,
                            description='Found one or several matches for this id',
                            skip_none=True)
    def get(self, bestid):
        """Search for a Best item by its id in Elastic database

        Args:
            bestid (str): best if for an address, a street or a municipality

        Returns:
            list: json list of corresponding best objects
        """
        # raw = get_arg("raw", False)

        if "%2F" in bestid:
            bestid = unquote_plus(bestid)
        # log(f"{(bestid, a1, a2, a3, a4, a5)}")
        # if a1 and "http" in bestid:
        #      bestid += "/"
        # for a in [a1, a2, a3, a4, a5]:
        #     if a :
        #         bestid += "/"+a

        log(f"get by id: {bestid}")

        client = Elasticsearch(pelias.elastic_api)

        mtch = re.search(r"([a-z\.]+.be)/id/([a-z]+)/", bestid,  re.IGNORECASE)

        if mtch is None or len(mtch.groups()) != 2:
            namespace.abort(400, f"Cannot parse best id '{bestid}'")

        log(f"mtch[2].lower(): '{mtch[2].lower()}'")
        obj_type = None
        if mtch[2].lower() in ["address", "adres"]:
            obj_type = "address"
        elif mtch[2].lower() in ["streetname", "straatnaam"]:
            obj_type = "street"
        elif mtch[2].lower() in ["municipality", "gemeente"]:
            obj_type = "locality"
        else:
            namespace.abort(400, f"Object type '{mtch[2]}' not supported so far in '{bestid}'")

        try:
            resp = client.search(index="pelias", body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"layer": obj_type}},
                            {"prefix": {"source_id": {"value": bestid.lower()}}}
                        ]
                    }
                }
            })

            resp = resp["hits"]["hits"]

            if len(resp) == 0:
                return "Object not found", 204

            final_result = []
            for resp_item in resp:
                if "addendum" in resp_item["_source"] and "best" in resp_item["_source"]["addendum"]:
                    it = {"properties": {"addendum": {"best": json.loads(resp_item["_source"]["addendum"]["best"])}}}
                    if "center_point" in resp_item["_source"]:
                        it["geometry"] = {"coordinates": resp_item["_source"]["center_point"]}
                    it["name"] = resp_item["_source"]["name"]

                    final_result.append(it)

            final_result = {"features": final_result}

            return to_rest_guidelines(final_result, with_pelias_raw=False)
        except NotFoundError:
            pass
        except ConnectionRefusedError as exc:
            log("ES ConnectionRefusedError")
            log(exc)

            return f"Cannot connect to Elastic: {exc}", 500

        except ConnectionError as exc:
            log("ES ConnectionError")
            log(exc)

            return f"Cannot connect to Elastic: {exc}", 500
        # log("Not found !")
        return "Object not found", 204


@namespace.route('/health', methods=['GET'])
class Health(Resource):
    """ Check service status """
    @namespace.response(500, 'Internal Server error')
    @namespace.response(503, 'Service is "DOWN"')
    @namespace.response(200, 'Service is "UP" or "DEGRADED"')
    def get(self):
        """Health status

        Returns
        -------
        - {'status': 'DOWN'}: Pelias server does not answer (or gives an unexpected answer)
        - {'status': 'DEGRADED'}: Either Libpostal or Photon is down (or gives an unexpected answer). Geocoding is still possible as long as it does not requires one of those transformers
        - {'status': 'UP'}: Service works correctly

        """
        # Checking Pelias

        pelias_res = pelias.check()

        if pelias_res is False:
            log("Pelias not up & running")
            log(f"Pelias host: {pelias_host}")

            return {"status": "DOWN",
                    "details": {"errorMessage": "Pelias server does not answer",
                                "details": "Pelias server does not answer"}}, 503
        if pelias_res is not True:
            return {"status": "DOWN",
                    "details": {"errorMessage": "Pelias server answers, but gives an unexpected answer",
                                "details": f"Pelias answer: {pelias_res}"}}, 503

        # Checking Interpolation

        try:
            interp_res = pelias.interpolate(lat=50.83582,
                                            lon=4.33844,
                                            number=20,
                                            street="Avenue Fonsny")
            log(interp_res)
            if len({}) > 0 and "geometry" not in interp_res:
                return {
                    "status": "DEGRADED",
                    "details": {
                        "errorMessage": "Interpolation server answers, but gives an unexpected answer",
                        "details": f"Interpolation answer: {interp_res}"
                    }}, 200

        except Exception:
            return {"status": "DEGRADED",
                    "details": {"errorMessage": "Interpolation server does not answer",
                                "details": "Interpolation server does not answer"}}, 200

        return {"status": "UP"}, 200
