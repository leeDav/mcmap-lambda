# Description
Designed as an AWS lambda function to take an image and return a list of
times each biome colour occurs. Triggers when a new file is `PUT` to S3 bucket.

### Example output
On taking an image of a Minecraft map, it should return something like;

```
{"spawnColours": [{"count": 128, "biome": 16, "percent": 0.89}, {"count": 2124, "biome": 17, "percent": 14.79}, {"count": 424, "biome": 36, "percent": 2.95}, {"count": 344, "biome": 1, "percent": 2.39}, {"count": 8254, "biome": 2, "percent": 57.46}, {"count": 2154, "biome": 35, "percent": 15.0}, {"count": 76, "biome": 0, "percent": 0.53}, {"count": 860, "biome": 7, "percent": 5.99}], "data": [{"version": "1.11", "seed": "38"}], "worldColours": [{"count": 27732, "biome": 17, "percent": 9.13}, {"count": 1280, "biome": 164, "percent": 0.42}, {"count": 112, "biome": 129, "percent": 0.04}, {"count": 8664, "biome": 36, "percent": 2.85}, {"count": 21228, "biome": 1, "percent": 6.99}, {"count": 144, "biome": 18, "percent": 0.05}, {"count": 7424, "biome": 130, "percent": 2.44}, {"count": 14864, "biome": 16, "percent": 4.89}, {"count": 2432, "biome": 163, "percent": 0.8}, {"count": 43916, "biome": 35, "percent": 14.45}, {"count": 95524, "biome": 2, "percent": 31.44}, {"count": 13584, "biome": 7, "percent": 4.47}, {"count": 2640, "biome": 4, "percent": 0.87}, {"count": 18084, "biome": 24, "percent": 5.95}, {"count": 46221, "biome": 0, "percent": 15.21}]}
```

This has three objects;
- `data`: Meta data. Not used for anything significant yet.
- `spawnColours`: An array of colours (by biome) in the possible spawning area*
- `worldColours`: As above, but for the entire map.

Each colour array is a collection of objects that have the following properties;
- `count`: The number of times the colour occurs in the image
- `biome`: An ID referring to the biome the colour relates to
- `percent`: The above `count` total converted to a percentage, rounded to 2DP

* Possible spawn area is unknown without analysing the `level.dat` file. As I'm
not doing that, I'm taking the coordinates from the multiplayer spawn as
[defined in this wiki page](http://minecraft.gamepedia.com/Spawn/Multiplayer_details).

# Notes/Caveats
I'm new to Python, to please ignore the ugly code.

Comes with the `libjpeg` library built on a Vagrant Ubuntu box.

# Credit
[ChunkBase.com](http://chunkbase.com/) for all their hard work. It's from their
site I got the biome list and colours.
