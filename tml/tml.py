#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    A library which makes it possible to read, modify and save teeworlds
    map files.

    :copyright: 2010 by the TML Team, see AUTHORS for more details.
    :license: GNU GPL, see LICENSE for more details.
"""

import os
from struct import unpack, pack
from zlib import decompress, compress

import PIL.Image
import PIL.ImageChops

from constants import ITEM_TYPES, LAYER_TYPES
import items

def int32(x):
    if x>0xFFFFFFFF:
        raise OverflowError
    if x>0x7FFFFFFF:
        x=int(0x100000000-x)
        if x<2147483648:
            return -x
        else:
            return -2147483648
    return x

class Header(object):
    """Contains fileheader information.

    Takes a file as argument, please make sure it is at the beginning.
    Note that the file won't be rewinded!
    """

    def __init__(self, teemap, f=None):
        self.teemap = teemap
        self.version = 4
        self.size = 0
        if f != None:
            sig = ''.join(unpack('4c', f.read(4)))
            if sig not in ('DATA', 'ATAD'):
                raise TypeError('Invalid signature')
            self.version, self.size_, self.swaplen, self.num_item_types, \
            self.num_items, self.num_raw_data, self.item_size, \
            self.data_size = unpack('8i', f.read(32))

            if self.version != 4:
                raise TypeError('Wrong version')

            # calculate the size of the whole header
            self.size = self.num_item_types * 12
            self.size += (self.num_items + self.num_raw_data) * 4
            self.size += self.num_raw_data * 4
            self.size += self.item_size

        # why the hell 36?
        self.size += 36

    def write(self, f):
        """Write the header itself in tw map format to a file.

        It calculates the item sizes. Every item consists of a special number of
        ints plus two additional ints which are added later (this is the +8).
        There is allways one envpoint item and one version item. All other items
        counted.
        """

        # count all items
        teemap = self.teemap
        item_size = len(teemap.layers + teemap.groups + teemap.images + \
                        teemap.envelopes) + 1 # 1 = envpoint item
        # calculate compressed data size and store the compressed data
        datas = []
        data_size = 0
        for data in teemap.compressed_data:
            data_size += len(data)
            datas.append(data)
        # calculate the item size
        layers_size = 0
        for layer in teemap.layers:
            if LAYER_TYPES[layer.type] == 'tile':
                layers_size += items.TileLayer.size
            else:
                layers_size += items.QuadLayer.size
        version_size = 4+8
        envelopes_size = len(teemap.envelopes)*items.Envelope.size
        groups_size = len(teemap.groups)*items.Group.size
        envpoints_size = len(teemap.envpoints)*24+8
        images_size = len(teemap.images)*items.Image.size
        item_size = version_size+groups_size+layers_size+envelopes_size \
                    +images_size+envpoints_size
        num_items = len(teemap.envelopes + teemap.groups + teemap.layers + \
                        teemap.images) + 2 # 2 = version item + envpoint item
        num_item_types = 2 # version and envpoints
        for type_ in ITEM_TYPES[2:]:
            if type_ == 'envpoint':
                continue
            name = ''.join([type_, 's'])
            if getattr(teemap, name):
                num_item_types += 1
        num_raw_data = len(datas)
        # calculate some other sizes
        header_size = 36
        type_size = num_item_types*12
        offset_size = (num_items+2*num_raw_data)*4
        size = header_size+type_size+offset_size+item_size+data_size-16
        swaplen = size-data_size

        f.write(pack('4c', *'DATA'))
        f.write(pack('8i', 4, size, swaplen, num_item_types, num_items,
                           num_raw_data, item_size, data_size))

class Teemap(object):

    def __init__(self, map_path=None):
        self.name = ''
        self.header = Header(self)

        # default list of item types
        for type_ in ITEM_TYPES:
            if type_ != 'layer':
                setattr(self, ''.join([type_, 's']), [])

        if map_path:
            self.load(map_path)
        else:
            self.create_default()

    @property
    def layers(self):
        """Returns a list of all layers, collected from the groups."""
        layers_ = []
        for group in self.groups:
            layers_.extend(group.layers)
        return layers_

    @property
    def gamelayer(self):
        """Just returns the gamelayer."""
        for layer in self.layers:
            if layer.is_gamelayer:
                return layer

    @property
    def width(self):
        return self.gamelayer.width

    @property
    def height(self):
        return self.gamelayer.height

    def load(self, map_path):
        """Load a new teeworlds map from `map_path`."""

        path, filename = os.path.split(map_path)
        self.name, extension = os.path.splitext(filename)
        if extension == '':
            map_path = os.extsep.join([map_path, 'map'])
        elif extension != ''.join([os.extsep, 'map']):
            raise TypeError('Invalid file')
        with open(map_path, 'rb') as f:
            self.header = Header(self, f)
            self.item_types = []
            for i in range(self.header.num_item_types):
                val = unpack('3i', f.read(12))
                self.item_types.append({
                    'type': val[0],
                    'start': val[1],
                    'num': val[2],
                })
            fmt = '{0}i'.format(self.header.num_items)
            self.item_offsets = unpack(fmt, f.read(self.header.num_items * 4))
            fmt = '{0}i'.format(self.header.num_raw_data)
            self.data_offsets = unpack(fmt, f.read(self.header.num_raw_data * 4))

            # "data uncompressed size"
            # print repr(f.read(self.header.num_raw_data * 4))

            data_start_offset = self.header.size
            item_start_offset = self.header.size - self.header.item_size

            self.compressed_data = []
            f.seek(data_start_offset)
            for offset in (self.data_offsets + (self.header.data_size,)):
                if offset > 0:
                    self.compressed_data.append(f.read(offset - last_offset))
                last_offset = offset

            # calculate with the offsets and the whole item size the size of
            # each item
            sizes = []
            for offset in self.item_offsets + (self.header.item_size,):
                if offset > 0:
                    sizes.append(offset - last_offset)
                last_offset = offset

            f.seek(item_start_offset)
            itemlist = []
            for item_type in self.item_types:
                for i in range(item_type['num']):
                    size = sizes[item_type['start'] + i]
                    item = items.Item(item_type['type'])
                    item.load(f.read(size), self.compressed_data)
                    itemlist.append(item)

            # order the items
            for type_ in ITEM_TYPES:
                # envpoints and layers will be handled separately
                if type_ in ('envpoint', 'layer'):
                    pass
                else:
                    name = ''.join([type_, 's'])
                    class_ = getattr(items, type_.title())
                    setattr(self, name, [class_(item) for item in itemlist
                                        if item.type == type_])

            # handle envpoints and layers
            self.envpoints = []
            layers = []
            for item in itemlist:
                # divide the envpoints item into the single envpoints
                if item.type == 'envpoint':
                    for i in range((len(item.info)-2) / 6):
                        info = item.info[2+(i*6):2+(i*6+6)]
                        self.envpoints.append(items.Envpoint(info))
                elif item.type == 'layer':
                    layer = items.Layer(item)
                    layerclass = ''.join([LAYER_TYPES[layer.type].title(),
                                         'Layer'])
                    class_ = getattr(items, layerclass)
                    layers.append(class_(item, self.images))

            # assign layers to groups
            for group in self.groups:
                start = group.start_layer
                end = group.start_layer + group.num_layers
                group.layers = [layer for layer in layers[start:end]]

        # usefull for some people like bnn :P
        return self

    def save(self, map_path='unnamed'):
        """Save the current map to `map_path`."""

        path, filename = os.path.split(map_path)
        self.name, extension = os.path.splitext(filename)
        if extension != ''.join([os.extsep, 'map']):
            map_path = ''.join([map_path, os.extsep, 'map'])
        with open(map_path, 'wb') as f:
            # get types
            item_types_data = []
            count = 0
            for i, item_type in enumerate(ITEM_TYPES):
                if item_type == 'info':
                    continue
                elif item_type in ('version', 'envpoint'):
                    item_types_data.append({
                        'type': i,
                        'start': count,
                        'num': 1
                    })
                    count += 1
                    continue
                name = ''.join([item_type, 's'])
                typelist = getattr(self, name)
                if typelist:
                    item_types_data.append({
                        'type': i,
                        'start': count,
                        'num': len(typelist)
                    })
                    count += len(typelist)

            # get items and create simultaneously a list of the corresponding
            # datas
            itemdata = []
            item_types = []
            datas = []
            for i, item_type in enumerate(ITEM_TYPES):
                if item_type == 'version':
                    itemdata.append(i) # type and id
                    itemdata.append(4) # size
                    itemdata.append(1) # version
                    item_types.append('version')
                elif item_type == 'envpoint':
                    itemdata.append(i<<16)
                    itemdata.append(len(self.envpoints)*6*4)
                    for envpoint in self.envpoints:
                        itemdata.append(envpoint.time)
                        itemdata.append(envpoint.curvetype)
                        for value in envpoint.values:
                            itemdata.append(value)
                    item_types.append('envpoint')
                elif item_type == 'image':
                    for id_, image in enumerate(self.images):
                        itemdata.append((i<<16)|id_)
                        image_data = image.get_data(len(datas))
                        for data in image_data:
                            datas.append(data)
                        itemdata.extend(image.itemdata)
                        item_types.append('image')
                elif item_type == 'envelope':
                    for id_, envelope in enumerate(self.envelopes):
                        itemdata.append((i<<16)|id_)
                        itemdata.extend(envelope.itemdata)
                        name = envelope.string_to_ints()
                        for int_ in name:
                            itemdata.append(int_)
                        item_types.append('envelope')
                elif item_type == 'group':
                    num_layers = 0
                    for id_, group in enumerate(self.groups):
                        # calculate new start_layer values
                        group.start_layer = num_layers
                        num_layers += len(group.layers)
                        itemdata.append((i<<16)|id_)
                        itemdata.extend(group.itemdata)
                        item_types.append('group')
                elif item_type == 'layer':
                    for id_, layer in enumerate(self.layers):
                        itemdata.append((i<<16)|id_)
                        data = layer.get_data(len(datas))
                        itemdata.extend(layer.itemdata)
                        name = '_'.join((LAYER_TYPES[layer.type], item_type))
                        item_types.append(name)
                        format = 'i' if name == 'quad_layer' else 'B'
                        fmt = '{0}{1}'.format(len(data), format)
                        data = pack(fmt, *data)
                        datas.append(data)

            # compress data
            self.compressed_data = [compress(data) for data in datas]

            # write header
            self.header.write(f)

            # write types
            for item_type in item_types_data:
                f.write(pack('3i', item_type['type'], item_type['start'], item_type['num']))

            # write item offsets
            item_offsets = []
            item_cur_offset = 0
            for type_ in item_types:
                item_offsets.append(item_cur_offset)
                if type_ == 'envpoint':
                    pass
                elif type_ == 'tile_layer':
                    item_cur_offset += items.TileLayer.size
                elif type_ == 'quad_layer':
                    item_cur_offset += items.QuadLayer.size
                else:
                    item_cur_offset += getattr(items, type_.title()).size
            for item_offset in item_offsets:
                f.write(pack('i', item_offset))

            # write data offsets
            data_cur_offset = 0
            for data in self.compressed_data:
                f.write(pack('i', data_cur_offset))
                data_cur_offset += len(data)

            # write uncompressed data sizes
            for data in datas:
                f.write(pack('i', len(data)))

            # finally write items
            for data in itemdata:
                f.write(pack('i', int32(data)))

            # compress data and write it
            for data in self.compressed_data:
                f.write(data)

            f.close()

    def create_default(self):
        """Creates the default map.

        The default map consists out of two groups containing a quadlayer
        with the background and the game layer.
        """

        self.groups = []
        background_group = items.Group()
        self.groups.append(background_group)
        background_group.default_background()
        background_layer = items.QuadLayer()
        background_layer.add_background_quad()
        background_group.layers.append(background_layer)
        game_group = items.Group()
        self.groups.append(game_group)
        game_layer = items.TileLayer(game=1)
        game_group.layers.append(game_layer)

    def _render_on_top(self, img1, img2):
        region = (0, 0, img1.size[0], img1.size[1])
        # create a transparent layer the size of the image and draw the
        # tile-/quadlayer in that layer.
        im = PIL.Image.new('RGBA', img1.size, (0,0,0,0))
        im.paste(img2, (0, 0))
        mask = im#.convert('1') # TODO: transparency bug
        return PIL.ImageChops.composite(im, img1, mask)

    def render(self, max_size=(5000, 5000), gamelayer_on_top=False):
        """Renders all tilelayers together.

        The returned value is a PIL Image which can e.g. be saved with
        ``image.save('path')``

        :param max_size: Tupel containing maximum width and height for image.
                         Pass (0, 0) to not resize the image. This can eat up
                         huge amounts of memory, depending on the map size!
        :param gamelayer_on_top: Decide if the gamelayer should be placed on
                                 top
        """
        # computer the new tile and map size
        old_ratio = self.width / float(self.height)
        new_ratio = max_size[0] / float(max_size[1])
        if new_ratio < old_ratio:
            width = max_size[0] / 64 * 64
            tilesize = width / self.width
        else:
            height = max_size[1] / 64 * 64
            tilesize = height / self.height
        width = self.width * tilesize
        height = self.height * tilesize
        if tilesize < 1:
            raise ValueError('Map to big for this scaling (one tile < 1 pixel)')

        im = PIL.Image.new('RGBA', (width, height))
        for layer in self.layers:
            if layer.is_gamelayer and gamelayer_on_top:
                continue
            if hasattr(layer, 'render'):
                im = self._render_on_top(im, layer.render(tilesize))
        if gamelayer_on_top:
                im = self._render_on_top(im, self.gamelayer.render(tilesize))
        return im

    def __repr__(self):
        return '<Teemap {0} ({1}x{2})>'.format(self.name, self.width,
                                                self.height)

if __name__ == '__main__':
    t = Teemap()
    t.load('dm1_test')
    t.save()
