import os
import errno
import json
import urllib
import urllib2
import socket
import csv

from urlparse import urlparse
from zipfile import ZipFile

from osgeo import ogr, osr
ogr.UseExceptions()

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('oa')

def mkdirsp(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise

class ExtractionError(Exception):
    pass

class ExtractionTask(object):
    @classmethod
    def from_type_string(clz, type_string):
        if type_string.lower() == 'http':
            return Urllib2ExtractionTask()
        elif type_string.lower() == 'ftp':
            return Urllib2ExtractionTask()
        elif type_string.lower() == 'esri':
            return EsriRestExtractionTask()
        else:
            raise KeyError("I don't know how to extract for type {}".format(type_string))

    def extract(self, source_urls):
        raise NotImplementedError()


class Urllib2ExtractionTask(ExtractionTask):
    USER_AGENT = 'openaddresses-extract/1.0 (https://github.com/openaddresses/openaddresses)'
    CHUNK = 16 * 1024

    logger = logger.getChild('urllib2')

    def extract(self, source_key, source_urls):
        output_files = []
        download_path = os.path.join('.', 'workdir', source_key, 'http')
        mkdirsp(download_path)

        for source_url in source_urls:
            file_path = os.path.join(download_path, source_url.split('/')[-1])

            if os.path.exists(file_path):
                output_files.append(file_path)
                self.logger.debug("File exists %s", file_path)
                continue

            self.logger.debug("Requesting %s", source_url)
            headers = {'User-Agent': self.USER_AGENT}

            try:
                req = urllib2.Request(source_url, headers=headers)
                resp = urllib2.urlopen(req)
            except urllib2.URLError as e:
                raise ExtractionError("Could not connect to URL", e)


            size = 0
            with open(file_path, 'wb') as fp:
                while True:
                    chunk = resp.read(self.CHUNK)
                    size += len(chunk)
                    if not chunk:
                        break
                    fp.write(chunk)

            output_files.append(file_path)

            self.logger.info("Downloaded %s bytes for file %s", size, file_path)

        return output_files


class EsriRestExtractionTask(ExtractionTask):
    USER_AGENT = 'openaddresses-extract/1.0 (https://github.com/openaddresses/openaddresses)'

    logger = logger.getChild('urllib2')

    def convert_esrijson_to_geojson(self, geom_type, esri_feature):
        if geom_type == 'esriGeometryPoint':
            geometry = {
                "type": "Point",
                "coordinates": [
                    esri_feature['geometry']['x'],
                    esri_feature['geometry']['y']
                ]
            }
        elif geom_type == 'esriGeometryMultipoint':
            geometry = {
                "type": "MultiPoint",
                "coordinates": [
                    [geom[0], geom[1]] for geom in esri_feature['geometry']['points']
                ]
            }
        else:
            raise KeyError("Don't know how to convert esri geometry type {}".format(geom_type))

        return {
            "type": "Feature",
            "properties": esri_feature.get('attributes'),
            "geometry": geometry
        }

    def extract(self, source_key, source_urls):
        output_files = []
        download_path = os.path.join('.', 'workdir', source_key, 'esri')
        mkdirsp(download_path)

        for source_url in source_urls:
            size = 0
            parts = urlparse(source_url)
            file_path = os.path.join(download_path, parts.netloc + '.json')

            if os.path.exists(file_path):
                output_files.append(file_path)
                self.logger.debug("File exists %s", file_path)
                continue

            with open(file_path, 'w') as f:
                f.write('{\n"type": "FeatureCollection",\n"features": [\n')
                start = 0
                width = 500
                while True:
                    query_url = source_url + '/query'
                    query_args = urllib.urlencode({
                        'where': 'objectid >= {} and objectid < {}'.format(start, (start + width)),
                        'geometryPrecision': 7,
                        'returnGeometry': True,
                        'outSR': 4326,
                        'outFields': '*',
                        'f': 'JSON',
                    })
                    query_url += '?' + query_args

                    self.logger.debug("Requesting %s", query_url)
                    headers = {'User-Agent': self.USER_AGENT}

                    try:
                        req = urllib2.Request(query_url, headers=headers)
                        resp = urllib2.urlopen(req, timeout=10)
                    except urllib2.URLError as e:
                        raise ExtractionError("Could not connect to URL", e)
                    except socket.timeout as e:
                        raise ExtractionError("Timeout when connecting to URL", e)

                    data = json.load(resp)
                    geometry_type = data.get('geometryType')

                    features = data.get('features')

                    f.write(',\n'.join([
                        json.dumps(self.convert_esrijson_to_geojson(geometry_type, feature)) for feature in features
                    ]))

                    size += len(features)
                    if len(features) == 0:
                        break
                    else:
                        f.write(',\n')
                        start += width

                f.write('\n]\n}\n')
            self.logger.info("Downloaded %s ESRI features for file %s", size, file_path)
            output_files.append(file_path)
        return output_files


class DecompressionError(Exception):
    pass


class DecompressionTask(object):
    @classmethod
    def from_type_string(clz, type_string):
        if type_string == None:
            return NoopDecompressTask()
        elif type_string.lower() == 'zip':
            return ZipDecompressTask()
        else:
            raise KeyError("I don't know how to decompress for type {}".format(type_string))

    def extract(self, source_paths):
        raise NotImplementedError()


class NoopDecompressTask(DecompressionTask):
    def decompress(self, source_key, source_paths):
        return source_paths


class ZipDecompressTask(DecompressionTask):

    logger = logger.getChild('unzip')

    def decompress(self, source_key, source_paths):
        output_files = []
        expand_path = os.path.join('.', 'workdir', source_key, 'unzipped')
        mkdirsp(expand_path)

        for source_path in source_paths:
            with ZipFile(source_path, 'r') as z:
                for name in z.namelist():
                    expanded_file_path = z.extract(name, expand_path)
                    self.logger.debug("Expanded file %s", expanded_file_path)
                    output_files.append(expanded_file_path)
        return output_files


class ConvertToCsvTask(object):

    logger = logger.getChild('convert')

    known_types = ('.shp', '.json', '.csv', '.kml')

    def convert(self, source_key, source_paths):
        output_files = []
        convert_path = os.path.join('.', 'workdir', source_key, 'converted')
        mkdirsp(convert_path)

        for source_path in source_paths:
            filename = os.path.basename(source_path)
            basename, ext = os.path.splitext(filename)
            file_path = os.path.join(convert_path, basename + '.csv')


            if ext not in self.known_types:
                self.logger.debug("Skipping %s because I don't know how to convert it", source_path)
                continue
            if os.path.exists(file_path):
                output_files.append(file_path)
                self.logger.debug("File exists %s", file_path)
                continue

            in_datasource = ogr.Open(source_path, 0)
            in_layer = in_datasource.GetLayer()
            inSpatialRef = in_layer.GetSpatialRef()

            in_layer_defn = in_layer.GetLayerDefn()
            out_fieldnames = []
            for i in range(0, in_layer_defn.GetFieldCount()):
                field_defn = in_layer_defn.GetFieldDefn(i)
                out_fieldnames.append(field_defn.GetName())
            out_fieldnames.append('centroid')

            outSpatialRef = osr.SpatialReference()
            outSpatialRef.ImportFromEPSG(4326)
            coordTransform = osr.CoordinateTransformation(inSpatialRef, outSpatialRef)

            with open(file_path, 'w') as f:
                writer = csv.DictWriter(f, fieldnames=out_fieldnames)
                writer.writeheader()

                in_feature = in_layer.GetNextFeature()
                while in_feature:
                    row = dict()

                    for i in range(0, in_layer_defn.GetFieldCount()):
                        field_defn = in_layer_defn.GetFieldDefn(i)
                        row[field_defn.GetNameRef()] = in_feature.GetField(i)
                    geom = in_feature.GetGeometryRef()
                    geom.Transform(coordTransform)
                    row['centroid'] = geom.Centroid().ExportToWkt()

                    writer.writerow(row)

                    in_feature.Destroy()
                    in_feature = in_layer.GetNextFeature()

            in_datasource.Destroy()
            output_files.append(file_path)

        return output_files


class ConformTask(object):

    logger = logger.getChild('conform')

    def conform(self, source_key, source_paths):


def skip(k):
    if not k.startswith('us'):
        return True

for dirpath, dirnames, filenames in os.walk('/Users/iandees/Workspace/addresses/addresses/sources'):
    for f in filenames:
        if f.endswith('.json'):
            with open(os.path.join(dirpath, f), 'r') as sf:
                source_data = json.load(sf)
                source_key = f[:-5]

                if skip(source_key): continue

                # Download the source data
                try:
                    task = ExtractionTask.from_type_string(source_data.get('type'))

                    data_urls = source_data.get('data')
                    if not isinstance(data_urls, list):
                        data_urls = [data_urls]

                    filenames = task.extract(source_key, data_urls)
                except KeyError as e:
                    print "Don't know how to process {}: {}".format(f, e)
                    continue
                except ExtractionError as e:
                    print "Problem extracting {}: {}".format(f, e)
                    continue

                # Decompress the downloaded file
                try:
                    task = DecompressionTask.from_type_string(source_data.get('compression'))

                    filenames = task.decompress(source_key, filenames)
                except KeyError:
                    print "Don't know how to decompress {}".format(f)
                    continue

                # Convert the source data to a CSV
                task = ConvertToCsvTask()
                filenames = task.convert(source_key, filenames)

                # Now we map the fields in the CSV to OpenAddresses field names
                task = ConformTask()
                filenames = task.conform(source_key, filenames)

                print filenames