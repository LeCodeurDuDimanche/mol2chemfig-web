'''
My name is Bond. JAMES Bond.
'''

from copy import deepcopy, copy
from math import atan, tan, pi

from . import chemfig_mappings as cfm
from .common import debug

# bond stereo properties and valences
from indigo import Indigo

# Indigo.UP : stereo "up" bond
# Indigo.DOWN : stereo "down" bond
# Indigo.EITHER : stereo "either" bond
# Indigo.CIS : "Cis" double bond
# Indigo.TRANS : "Trans" double bond
# zero : not a stereo bond of any kind

# bond_type: 1,2,3,4 for single, double, triple, aromatic

# map indigo's bond specifiers to m2cf custom ones.
bond_mapping = {
    1 : 'single',
    2 : 'double',
    3 : 'triple',
    4 : 'aromatic', # not really used
    Indigo.UP : 'upto',
    Indigo.DOWN : 'downto',
    Indigo.EITHER : 'either'
}

def compare_positions(x1, y1, x2, y2):
    '''
    calculate distance and angle between the
    coordinates of two atoms.
    '''

    xdiff = x2 - x1
    ydiff = y2 - y1

    length = (xdiff**2 + ydiff**2) ** 0.5

    if xdiff == 0:
        if ydiff < 0:
            angle = 270
        else:
            angle = 90

    else:
        raw_angle = atan(abs(ydiff / xdiff)) * 180/pi

        if ydiff >= 0:
            if xdiff > 0:
                angle = raw_angle
            else:
                angle = 180 - raw_angle
        else:
            if xdiff > 0:
               angle = -raw_angle
            else:
                angle = 180 + raw_angle

    return length, angle


class Bond(object):
    '''
    helper class for molecule.Molecule

    a bond connects two atoms and computes its angle and length
    from those. It knows how to render itself to chemfig. Bonds
    can be hooks.

    On instantiation, the bond is not part of a hierarchy yet, so
    we can assign a parent. This has to occur later. So, initially
    we just know the start and the end atom.
    '''
    is_last = False         # flag for bond that is the last descendant of
                            # the exit bond - needed in rare case in
                            # cases for bond formatting.
    to_phantom = False      # flag for bonds that should render their end atoms
                            # as phantoms: Ring closures and cross bonds
    is_trunk = False        # by default, bonds are not part of the trunk
    parent = None           # will be assigned when bonds are added to the tree.
    clockwise = 0           # only significant in double bonds in rings that are
                            # not drawn with aromatic circles

    def __init__(self,
                 options,
                 start_atom,
                 end_atom,
                 bond_type=None,
                 stereo=0):

        self.options = options
        self.start_atom = start_atom
        self.end_atom = end_atom

        # special styles that get rendered via tikz
        self.tikz_styles = set()
        self.tikz_values = {}

        if stereo in (Indigo.UP, Indigo.DOWN):
            if self.options['flip_vertical'] != self.options['flip_horizontal']:
                stereo = Indigo.UP + Indigo.DOWN - stereo

        if stereo in (Indigo.UP, Indigo.DOWN, Indigo.EITHER): # implies single bond
            self.bond_type = bond_mapping[stereo]

        else: # no interesting stereo property - simply go with bond valence
              # or else keep passed-in string specifier
            self.bond_type = bond_mapping.get(bond_type, bond_type)

        # bonds now are also the nodes in the molecule tree. These two
        # attributes must be set later, when the tree is created.
        self.descendants = []

        self.length, angle = self.bond_dimensions()
        # length is adjusted and rounded later, after all is parsed

        # apply molecule rotation
        angle += self.options['rotate']
        self.angle = angle

        # define marker
        marker = self.options.get('markers', None)

        if marker is not None:
            ids = [self.start_atom.idx +1, self.end_atom.idx +1]
            ids.sort()
            self.marker = '%s%s-%s' % (marker, ids[0], ids[1])
        else:
            self.marker = ""


    def bond_dimensions(self):
        '''
        determine bond angle and distance between two atoms
        '''
        return compare_positions(
                                 self.start_atom.x,
                                 self.start_atom.y,
                                 self.end_atom.x,
                                 self.end_atom.y
                                )


    def is_clockwise(self, center_x, center_y):
        '''
        determine whether the bond will be drawn clockwise
        or counterclockwise relative to center
        '''
        if self.clockwise: # assign only once
            return

        center_dist, center_angle = compare_positions(
                                 self.end_atom.x,
                                 self.end_atom.y,
                                 center_x,
                                 center_y)

        # bond is already rotated at this stage, so we need to
        # rotate the ring center also
        center_angle += self.options['rotate']
        center_kink = (center_angle - self.angle) % 360

        if center_kink > 180:
            self.clockwise = 1
        else:
            self.clockwise = -1


    def clone(self):
        '''
        deepcopy but keep original atoms
        '''
        c = copy(self)
        # c.start_atom, c.and_atom = self.start_atom, self.end_atom

        return c


    def invert(self):
        '''
        draw a bond backwards.
        '''
        c = deepcopy(self)
        c.start_atom, c.end_atom = self.end_atom, self.start_atom
        c.angle = (c.angle + 180) % 360

        if self.bond_type == 'upto':
            c.bond_type = 'upfrom'
        elif self.bond_type == 'downto':
            c.bond_type = 'downfrom'

        return c


    def set_link(self):
        '''
        make this bond an invisible link. this also cancels
        any other tikz styles, and removes the marker.
        '''
        self.bond_type = "link"
        self.tikz_styles = set()
        self.tikz_values = {}
        self.marker = ""


    def set_cross(self, last=False):
        '''
        draw this bond crossing over another.
        '''
        # debug(self.start_atom.idx, self.end_atom.idx, self.tikz_styles, self.tikz_values)
        start_angles = self.upstream_angles()
        end_angles = self.downstream_angles()

        start_angle = min(start_angles.values())
        start = max(10, self.cotan100(start_angle))

        end_angle = min(end_angles.values())
        end = max(10, self.cotan100(end_angle))

        self.tikz_styles.add("cross")
        self.tikz_values.update( dict(bgstart=start, bgend=end))

        self.is_last = last


    def _adjoining_angles(self, atom, inversion_angle=0):
        '''
        determine the narrowest upstream or downstream angles
        on the left and the right.
        '''
        raw_angles = atom.bond_angles[:]
        raw_angles = [int(round(a)) % 360 for a in raw_angles]

        reference_angle = int(round(self.angle - inversion_angle)) % 360
        # debug(atom.idx, inversion_angle, reference_angle, raw_angles)

        raw_angles.remove(reference_angle)

        if not raw_angles:  # no other bonds attach to start atom
            return None, None

        angles = [(a - reference_angle) % 360 for a in raw_angles]
        angles.sort()

        return int(round(angles[0])), int(round(angles[-1] ))


    def upstream_angles(self):
        '''
        determine the narrowest upstream left and upstream right angle.
        '''
        first, last = self._adjoining_angles(self.start_atom)

        # for the right angle, convert outer to inner
        if last is not None:
            last = 360-last

        return dict(left=first, right=last)


    def downstream_angles(self):
        '''
        determine the narrowest downstream left and downstream right angle.
        '''
        first, last = self._adjoining_angles(self.end_atom, 180)

        if last is not None:
            # for the left angle, convert outer to inner
            last = 360-last

        return dict(left=last, right=first)


    def angle_penalty(self, angle):
        '''
        scoring function used in picking sides for second
        stroke of double bond
        '''
        if angle is None:
            return 0

        return (angle - 105) ** 2


    def cotan100(self,angle):
        '''
        100 times cotan of angle, rounded
        '''
        _tan = tan(angle * pi/180)
        return int(round(100/_tan))


    def shorten_stroke(self, same_angle, other_angle):
        '''
        determine by how much to shorten the second stroke
        of a double bond.
        '''
        if same_angle is None: # other_angle will be, too; don't shorten.
            return 0

        if same_angle <= 180:
            angle = 0.5 * same_angle
        else:
            if 210 < same_angle < 270:
                angle = same_angle - 180
            elif 210 < other_angle < 270:
                angle = other_angle - 180
            else:
                angle = 90

        return self.cotan100(angle)


    def fancy_double(self):
        '''
        work out the parameters for rendering a fancy double bond.

        we need to decide whether the second stroke should be to
        the left or the right of the main stroke, and also by
        how much to shorten the start and and of the second stroke.
        '''
        # if we are in a ring, the second stroke should be inside.

        start_angles = self.upstream_angles()
        end_angles = self.downstream_angles()

        # outside rings and if the double bond connects to explicit atoms,
        # plain symmetric double bonds tend to look better.

        if not self.clockwise and \
               (self.start_atom.explicit or self.end_atom.explicit):

            if self.start_atom.explicit and self.end_atom.explicit:
                return None

            elif self.start_atom.explicit and \
                 (end_angles['left'] is None or \
                   (90 <= abs(end_angles['left']) <= 135 and \
                    90 <= abs(end_angles['right']) <= 135)):
                       return None

            elif self.end_atom.explicit and \
                 (start_angles['left'] is None or \
                   (90 <= abs(start_angles['left']) <= 135 and \
                    90 <= abs(start_angles['right']) <= 135)):
                       return None

        # at this point we are looking at either only implicit atoms
        # or extreme angles.
        if self.clockwise == -1:
            side = "left"

        elif self.clockwise == 1:
            side = "right"

        else: # not in a ring. use scoring function to pick sides.
            _ap = self.angle_penalty

            left_penalty = _ap(start_angles['left']) + _ap(end_angles['left'])
            right_penalty = _ap(start_angles['right']) + _ap(end_angles['right'])

            if left_penalty < right_penalty:
                side = "left"
            elif left_penalty > right_penalty:
                side = "right"
            else: # penalties equal - try to pick sides consistently
                if abs(self.angle - 44.5) < 90:
                    side = "left"
                else:
                    side = "right"

        if self.start_atom.explicit:
            start = 0
        else:
            if side == 'left':
                start = self.shorten_stroke(start_angles['left'], start_angles['right'])
            else:
                start = self.shorten_stroke(start_angles['right'], start_angles['left'])

        if self.end_atom.explicit:
            end = 0
        else:
            if side == 'left':
                end = self.shorten_stroke(end_angles['left'], end_angles['right'])
            else:
                end = self.shorten_stroke(end_angles['right'], end_angles['left'])

        return side, start, end


    def fancy_triple(self):
        '''
        work out parameters for fancy triple bond. We don't
        need to choose sides here, just calculate the required
        line shortening.
        '''
        end_angles = self.downstream_angles()

        if self.start_atom.explicit:
            start = 0
        else:
            start_angles = list(self.upstream_angles().values())
            if start_angles[0] is not None:
                start = self.cotan100(0.5 * min(start_angles))
            else:
                start = 0

        if self.end_atom.explicit:
            end = 0
        else:
            end_angles = list(self.downstream_angles().values())
            if end_angles[0] is not None:
                end = self.cotan100(0.5 * min(end_angles))
            else:
                end = 0

        return start, end


    def bond_to_chemfig(self):
        '''
        delegate to chemfig_mappings module to render
        the bond code, without the atom
        '''

        if self.to_phantom:
            end_string_pos = self.end_atom.phantom_pos
        else:
            end_string_pos = self.end_atom.string_pos

        if self.options['fancy_bonds'] \
           and self.bond_type in ('double', 'triple'):

            # debug("b2c before ", self.start_atom.idx, self.end_atom.idx, self.tikz_styles, self.tikz_values)

            if self.bond_type == 'double':
                fd = self.fancy_double()

                if fd is not None:
                    side, start, end = fd

                    self.tikz_styles.add("double")
                    self.tikz_styles.add(side)
                    self.tikz_values.update( dict(start=start, end=end) )
                    self.bond_type = 'decorated'

            elif self.bond_type == 'triple':
                self.tikz_styles.add('triple')
                start, end = self.fancy_triple()

                self.tikz_values.update( dict(start=start, end=end) )
                self.bond_type = 'decorated'

            # debug("b2c after ", self.start_atom.idx, self.end_atom.idx, self.tikz_styles, self.tikz_values)

        code = cfm.format_bond(
                    self.options,
                    self.angle,
                    self.parent.angle,
                    self.bond_type,
                    self.clockwise,
                    self.is_last,
                    self.length,
                    self.start_atom.string_pos,
                    end_string_pos,
                    self.tikz_styles,
                    self.tikz_values,
                    self.marker
               )

        return code

    def indent(self, level, bond_code, atom_code='', comment_code=''):
        stuff = ' ' * self.options['indent'] * level \
                     + bond_code.rjust(cfm.BOND_CODE_WIDTH) \
                     + atom_code

        if comment_code:
            stuff += "% " + comment_code

        return stuff.rstrip()


    def render(self, level):
        '''
        render bond and trailing atom.
        '''
        if not self.to_phantom:
            atom_code, comment_code = self.end_atom.render()
        else:
            atom_code, comment_code = self.end_atom.render_phantom()

        bond_code = self.bond_to_chemfig()

        return self.indent(level, bond_code, atom_code, comment_code)


class DummyFirstBond(Bond):
    '''
    semi-dummy class that only takes an endatom, wich is the
    first atom in the molecule, and just renders that.

    The other dummy attributes only exist to play nice with
    the molecule class.
    '''

    def __init__(self, options, end_atom):
        self.options = options
        self.end_atom = end_atom
        self.angle = None
        self.descendants = []
        self.length = None

    def bond_to_chemfig(self):
        return ''               # empty bond code before first atom


class AromaticRingBond(Bond):
    '''
    A gross hack to render the circle inside an aromatic ring
    as a node in the regular bond hierarchy.
    '''
    descendants = []
    scale = 1.5             # 1.5 corresponds to chemfig's ring size

    def __init__(self,  options, parent, angle, length, inner_r):
        self.options = options
        self.angle = cfm.num_round(angle,1) % 360
        if parent is not None:
            self.parent_angle = parent.angle
        else:
            self.parent_angle = None
        self.length = cfm.num_round(length, 2)
        self.radius = cfm.num_round(self.scale * inner_r, 2)

    def render(self, level):
        '''
        there is no atom to render, so we just call chemfig_mapping
        on our own attributes.
        '''
        ring_bond_code, ring_code, comment = cfm.format_aromatic_ring(
                                                             self.options,
                                                             self.angle,
                                                             self.parent_angle,
                                                             self.length,
                                                             self.radius)

        return self.indent(level, ring_bond_code, ring_code, comment)


