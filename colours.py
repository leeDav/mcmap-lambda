import json, math, boto3
from PIL import Image
from operator import itemgetter

biomes = [{
    "biomeID": 0,
    "name": "Ocean",
    "rgb": "0, 0, 112"
}, {
    "biomeID": 1,
    "name": "Plains",
    "rgb": "141, 179, 96"
}, {
    "biomeID": 2,
    "name": "Desert",
    "rgb": "250, 148, 24"
}, {
    "biomeID": 3,
    "name": "Extreme Hills",
    "rgb": "96, 96, 96"
}, {
    "biomeID": 4,
    "name": "Forest",
    "rgb": "5, 102, 33"
}, {
    "biomeID": 5,
    "name": "Taiga",
    "rgb": "11, 102, 89"
}, {
    "biomeID": 6,
    "name": "Swampland",
    "rgb": "7, 249, 178"
}, {
    "biomeID": 7,
    "name": "River",
    "rgb": "0, 0, 255"
}, {
    "biomeID": 8,
    "name": "Hell",
    "rgb": "255, 0, 0"
}, {
    "biomeID": 9,
    "name": "The End",
    "rgb": "128, 128, 255"
}, {
    "biomeID": 10,
    "name": "FrozenOcean",
    "rgb": "144, 144, 160"
}, {
    "biomeID": 11,
    "name": "FrozenRiver",
    "rgb": "160, 160, 255"
}, {
    "biomeID": 12,
    "name": "Ice Plains",
    "rgb": "255, 255, 255"
}, {
    "biomeID": 13,
    "name": "Ice Mountains",
    "rgb": "160, 160, 160"
}, {
    "biomeID": 14,
    "name": "MushroomIsland",
    "rgb": "255, 0, 255"
}, {
    "biomeID": 15,
    "name": "MushroomIslandShore",
    "rgb": "160, 0, 255"
}, {
    "biomeID": 16,
    "name": "Beach",
    "rgb": "250, 222, 85"
}, {
    "biomeID": 17,
    "name": "DesertHills",
    "rgb": "210, 95, 18"
}, {
    "biomeID": 18,
    "name": "ForestHills",
    "rgb": "34, 85, 28"
}, {
    "biomeID": 19,
    "name": "TaigaHills",
    "rgb": "22, 57, 51"
}, {
    "biomeID": 20,
    "name": "Extreme Hills Edge",
    "rgb": "114, 120, 154"
}, {
    "biomeID": 21,
    "name": "Jungle",
    "rgb": "83, 123, 9"
}, {
    "biomeID": 22,
    "name": "JungleHills",
    "rgb": "44, 66, 5"
}, {
    "biomeID": 23,
    "name": "JungleEdge",
    "rgb": "98, 139, 23"
}, {
    "biomeID": 24,
    "name": "Deep Ocean",
    "rgb": "0, 0, 48"
}, {
    "biomeID": 25,
    "name": "Stone Beach",
    "rgb": "162, 162, 132"
}, {
    "biomeID": 26,
    "name": "Cold Beach",
    "rgb": "250, 240, 192"
}, {
    "biomeID": 27,
    "name": "Birch Forest",
    "rgb": "48, 116, 68"
}, {
    "biomeID": 28,
    "name": "Birch Forest Hills",
    "rgb": "31, 95, 50"
}, {
    "biomeID": 29,
    "name": "Roofed Forest",
    "rgb": "64, 81, 26"
}, {
    "biomeID": 30,
    "name": "Cold Taiga",
    "rgb": "49, 85, 74"
}, {
    "biomeID": 31,
    "name": "Cold Taiga Hills",
    "rgb": "36, 63, 54"
}, {
    "biomeID": 32,
    "name": "Mega Taiga",
    "rgb": "89, 102, 81"
}, {
    "biomeID": 33,
    "name": "Mega Taiga Hills",
    "rgb": "69, 79, 62"
}, {
    "biomeID": 34,
    "name": "Extreme Hills+",
    "rgb": "80, 112, 80"
}, {
    "biomeID": 35,
    "name": "Savanna",
    "rgb": "189, 178, 95"
}, {
    "biomeID": 36,
    "name": "Savanna Plateau",
    "rgb": "167, 157, 100"
}, {
    "biomeID": 37,
    "name": "Mesa",
    "rgb": "217, 69, 21"
}, {
    "biomeID": 38,
    "name": "Mesa Plateau F",
    "rgb": "176, 151, 101"
}, {
    "biomeID": 39,
    "name": "Mesa Plateau",
    "rgb": "202, 140, 101"
}, {
    "biomeID": 129,
    "name": "Sunflower Plains",
    "rgb": "181, 219, 136"
}, {
    "biomeID": 130,
    "name": "Desert M",
    "rgb": "255, 188, 64"
}, {
    "biomeID": 131,
    "name": "Extreme Hills M",
    "rgb": "136, 136, 136"
}, {
    "biomeID": 132,
    "name": "Flower Forest",
    "rgb": "106, 116, 37"
}, {
    "biomeID": 133,
    "name": "Taiga M",
    "rgb": "51, 142, 129"
}, {
    "biomeID": 134,
    "name": "Swampland M",
    "rgb": "47, 255, 218"
}, {
    "biomeID": 140,
    "name": "Ice Plains Spikes",
    "rgb": "210, 255, 255"
}, {
    "biomeID": 149,
    "name": "Jungle M",
    "rgb": "123, 163, 49"
}, {
    "biomeID": 151,
    "name": "JungleEdge M",
    "rgb": "138, 179, 63"
}, {
    "biomeID": 155,
    "name": "Birch Forest M",
    "rgb": "88, 156, 108"
}, {
    "biomeID": 156,
    "name": "Birch Forest Hills M",
    "rgb": "71, 135, 90"
}, {
    "biomeID": 157,
    "name": "Roofed Forest M",
    "rgb": "104, 121, 66"
}, {
    "biomeID": 158,
    "name": "Cold Taiga M",
    "rgb": "89, 125, 114"
}, {
    "biomeID": 160,
    "name": "Mega Spruce Taiga",
    "rgb": "129, 142, 121"
}, {
    "biomeID": 161,
    "name": "Redwood Taiga Hills M",
    "rgb": "129, 142, 121"
}, {
    "biomeID": 162,
    "name": "Extreme Hills+ M",
    "rgb": "120, 152, 120"
}, {
    "biomeID": 163,
    "name": "Savanna M",
    "rgb": "229, 218, 135"
}, {
    "biomeID": 164,
    "name": "Savanna Plateau M",
    "rgb": "207, 197, 140"
}, {
    "biomeID": 165,
    "name": "Mesa (Bryce)",
    "rgb": "255, 109, 61"
}, {
    "biomeID": 166,
    "name": "Mesa Plateau F M",
    "rgb": "216, 191, 141"
}, {
    "biomeID": 167,
    "name": "Mesa Plateau M",
    "rgb": "242, 180, 141"
}]

def getBiomeFromColour(rgb):
    for biome in biomes:
        if rgb == biome['rgb']:
            return biome['biomeID']

def formatColours(colours):
    lst = []
    total = sum([int(i[0]) for i in colours])

    for i in colours:
        calc = "{0:.2f}".format(float(i[0]) / float(total) * 100)
        percentage = "{0:.2f}".format(float(i[0]) / float(total) * 100)
        newColor = ', '.join(str(x) for x in i[1][:-1])

        lst.append({'count': i[0],
                    'percent': float(percentage),
                    'biome': getBiomeFromColour(newColor)})

    return lst

def getSpawnColours(im):
    half_the_width = im.size[0] / 2
    half_the_height = im.size[1] / 2

    im = im.crop(
        (
            half_the_width - 57,
            half_the_height - 63,
            half_the_width + 57,
            half_the_height + 63
        )
    )

    return formatColours(im.getcolors())

def lambda_handler(event, context):
    bucket = event["Records"][0]["s3"]["bucket"]["name"]
    seed = event["Records"][0]["s3"]["object"]["key"]
    newList = {}
    newList['data'] = [{'version': '1.11', 'seed': seed}]

    s3_client = boto3.client('s3')
    s3_client.download_file(bucket, seed, "/tmp/tmp.png")

    im = Image.open("/tmp/tmp.png")
    newList['worldColours'] = formatColours(im.getcolors())
    newList['spawnColours'] = getSpawnColours(im)

    response = {
        "statusCode": 200,
        "body": json.dumps(newList)
    }
    print json.dumps(newList)
    return response
