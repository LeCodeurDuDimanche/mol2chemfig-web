'''
parse a molfile molecule and render to chemfig code
'''

import math, sys

from . import chemfig_mappings as cfm
from .common import MCFError, Counter, debug

from .atom import Atom
from .bond import Bond, DummyFirstBond, AromaticRingBond, compare_positions

from indigo import IndigoException

class Molecule(object):

    bond_scale = 1.0        # can be overridden by user option
    exit_bond = None        # the first bond in the tree that connects to the exit atom

    def __init__(self, options, tkmol):
        self.options = options
        self.tkmol = tkmol

        self.atoms = self.parseAtoms()

        # now it's time to flip and flop the coordinates
        for atom in list(self.atoms.values()):
            if self.options['flip_horizontal']:
                atom.x = -atom.x
            if self.options['flip_vertical']:
                atom.y = -atom.y

        self.bonds, self.atom_pairs = self.parseBonds()

        # work out the angles for each atom - this is used for
        # positioning of implicit hydrogens and charges.

        for connection, bond in list(self.bonds.items()):
            first_idx, last_idx = connection
            self.atoms[first_idx].bond_angles.append(bond.angle)

        # this would be the place to work out the placement of the second
        # and third strokes.

        # connect fragments, if any, with invisible bonds. By doing this
        # AFTER assigning bond angles, we prevent these invisible bonds
        # from interfering with placement of hydrogens or charges.
        self.connect_fragments()  # connect fragments or isolated atoms

        # arrange the bonds into a tree
        self.seen_atoms = set()
        self.seen_bonds = set()

        self.entry_atom, self.exit_atom = self.pickFirstLastAtoms()
        self.root = self.parseTree(start_atom=None, end_atom=self.entry_atom)


        if len(self.atoms) > 1:
            if self.exit_atom is None:  # pick a default exit atom if needed
                self.exit_bond = self.default_exit_bond()
                self.exit_atom = self.exit_bond.end_atom

            # flag all atoms between the entry atom and the exit atom - these
            # will be part of the trunk, all others will be rendered as branches
            if self.entry_atom is not self.exit_atom:
                flagged_bond = self.exit_bond

                while flagged_bond.end_atom is not self.entry_atom:
                    flagged_bond.is_trunk = True
                    flagged_bond = flagged_bond.parent

            # process cross bonds
            if self.options['cross_bond'] is not None:
                self.process_cross_bonds()

            # adjust bond lengths
            self.scaleBonds()

            # modify bonds in rings
            self.annotateRings()

        # let each atom work out its preferred quadrant for placing
        # hydrogens or charges
        for atom in list(self.atoms.values()):
            atom.score_angles()

        # finally, render the thing and cache the result.
        self._rendered = self.render()


    def link_atoms(self, x, y):
        '''
        connect atoms with indexes x and y using a pseudo bond.
        Helper for connect_fragments
        '''
        start_atom = self.atoms[x]
        end_atom = self.atoms[y]

        bond = Bond(self.options, start_atom, end_atom)
        bond.set_link()

        self.bonds[(x, y)] = bond
        self.bonds[(y, x)] = bond.invert()

        start_atom.neighbors.append(y)
        end_atom.neighbors.append(x)


    def connect_fragments(self):
        '''
        connect multiple fragments, using link bonds across their
        last and first atoms, respectively.
        '''
        fragments = self.molecule_fragments()

        if len(fragments) > 1:

            for head, tail in zip(fragments[:-1], fragments[1:]):
                head_last = head[-1][-1]
                tail_first = tail[0][0]

                self.link_atoms(head_last, tail_first)

        # now look for orphaned single atoms
        atoms = set(self.atoms.keys())

        bonded = set()
        for pair in self.atom_pairs:
            bonded.update(pair)

        unbonded = list(atoms - bonded)

        if unbonded:
            if fragments:
                anchor = fragments[-1][-1][-1]
            else: # several atoms, but no bonds
                anchor, unbonded = unbonded[0], unbonded[1:]

            for atom in unbonded:
                self.link_atoms(anchor, atom)


    def molecule_fragments(self):
        '''
        identify unconnected fragments in the molecule.
        used by connect_fragments
        '''
        def split_pairs(pair_list):
            '''
            break up pair_list into one list that contains all pairs
            that are connected, directly or indirectly,  to the first
            pair in the list, and another list containing the rest.
            '''
            first, rest = pair_list[0], pair_list[1:]
            connected_atoms = set(first)

            connected_pairs = [first]

            while True:
                unconnected = []

                for r in rest:
                    s = set(r)

                    if connected_atoms & s:
                        connected_atoms |= s
                        connected_pairs.append(r)
                    else:
                        unconnected.append(r)

                if len(unconnected) == len(rest): # no new pairs found in this loop iteration
                    return connected_pairs, unconnected
                else:
                    rest = unconnected

        fragments = []

        atom_pairs = self.atom_pairs[:]

        if len(atom_pairs) == 0:
            return []
        elif len(atom_pairs) == 1:
            return [atom_pairs]

        while True:
            connected, rest = split_pairs(atom_pairs)
            fragments.append(connected)

            if not rest:
                return fragments
            else:
                atom_pairs = rest


    def treebonds(self,root=False):
        '''
        return a list with all bonds in the molecule tree
        '''
        allbonds = []

        def recurse(rt):
            allbonds.append(rt)
            for d in rt.descendants:
                recurse(d)

        recurse(self.root)

        if not root:
            allbonds = allbonds[1:]

        return allbonds


    def process_cross_bonds(self):
        '''
        if cross bonds have been declared:
        1. tag the corresponding bonds within the tree as no-ops
        2. create a ghost-bond connection from exit_atom to start atom
        3. create a drawn duplicate of the cross bond
        4. append 2 and 3 as branch to the exit atom

        this is unfortunately all a little hackish.
        '''
        cross_bonds = self.options['cross_bond']

        for start1, end1 in cross_bonds:
            start = start1 - 1
            end = end1 - 1

            # retrieve the matching bond that's in the parse tree
            for combo in ((start, end), (end, start)):
                if combo in self.seen_bonds:
                    bond = self.bonds[combo]
                    break
            else: # referenced bond doesn't exist
                raise MCFError("bond %s-%s doesn't exist" % (start1, end1))

            # very special case: the bond _might_ already be the very
            # last one to be rendered - then we just tag it
            if self.exit_bond.descendants and bond is self.exit_bond.descendants[-1]:
                bond.set_cross(last=True)
                continue

            # create a copy of the bond that will be rendered later
            bond_copy = bond.clone()

            # tag original bond as no-op
            bond.set_link()

            # modify bond copy
            bond_copy.set_cross()
            bond_copy.to_phantom = True     # don't render atom again
            bond_copy.descendants = []      # forget copied descendants

            if bond_copy.start_atom is not self.exit_atom: # usual case
                # create a pseudo bond from the exit atom to the start atom
                # pseudo bond will not be drawn, serves only to "move the pen"
                pseudo_bond = Bond(self.options,
                                self.exit_atom,
                                bond_copy.start_atom)

                pseudo_bond.set_link()
                pseudo_bond.to_phantom = True      # don't render the atom, either

                bond_copy.parent = pseudo_bond
                pseudo_bond.descendants.append(bond_copy)

                pseudo_bond.parent = self.exit_bond
                self.exit_bond.descendants.append(pseudo_bond)

            else: # occasionally, the molecule's exit atom may be the starting point
                  # of the elevated bond
                self.exit_bond.descendants.append(bond_copy)


    def default_exit_bond(self):
        '''
        pick the bond and atom that is at the greatest distance from
        the entry atom along the parsed molecule tree. This
        must be one of the leaf atoms, obviously.
        '''
        scored = []

        for bond in self.treebonds():
            if bond.to_phantom:   # don't pick phantom atoms as exit
                continue

            distance = 0

            the_bond = bond

            while the_bond is not None and the_bond.end_atom is not self.entry_atom:
                distance += 1
                the_bond = the_bond.parent

            scored.append((distance, len(bond.descendants), bond))

        scored.sort(key=lambda el: el[0])
        return scored[-1][-1]


    def pickFirstLastAtoms(self):
        '''
        If the first atom is not given, we try to pick one
        that has only one bond to the rest of the molecule,
        so that only the first angle is absolute.
        '''
        if self.options['entry_atom'] is not None:
            entry_atom = self.atoms.get(self.options['entry_atom'] - 1) # -> zero index
            if entry_atom is None:
                raise MCFError('Invalid entry atom number')

        else: # pick a default atom with few neighbors
            atoms = list(self.atoms.values())
            atoms.sort(key=lambda atom: len(atom.neighbors))
            entry_atom = atoms[0]

        if self.options['exit_atom'] is not None:
            exit_atom = self.atoms.get(self.options['exit_atom'] - 1) # -> zero index
            if exit_atom is None:
                raise MCFError('Invalid exit atom number')
        else:
            exit_atom = None

        return entry_atom, exit_atom


    def parseAtoms(self):
        '''
        Read some attributes from the toolkit atom object
        '''
        coordinates = []

        # wrap all atoms and supply coordinates
        wrapped_atoms = {}

        for ra in self.tkmol.iterateAtoms():
            idx = ra.index()
            element = ra.symbol()

            try:
                hydrogens = ra.countImplicitHydrogens()
            except IndigoException:
                if self.options['strict']:
                    raise
                hydrogens = 0

            charge = ra.charge()
            radical = ra.radicalElectrons()

            neighbors = [na.index() for na in ra.iterateNeighbors()]

            x, y, z = ra.xyz()

            wrapped_atoms[idx] = Atom(self.options,
                                      idx,
                                      x, y,
                                      element,
                                      hydrogens,
                                      charge,
                                      radical,
                                      neighbors)

        return wrapped_atoms


    def parseBonds(self):
        '''
        read some bond attributes
        '''
        bonds = {}        # dictionary with bond objects, both orientations
        atom_pairs = []   # atom index pairs only, unique

        for bond in self.tkmol.iterateBonds():
            # start, end, bond_type, stereo = numbers
            start = bond.source().index()
            end = bond.destination().index()

            bond_type = bond.bondOrder() # 1,2,3,4 for single, double, triple, aromatic
            stereo = bond.bondStereo()

            start_atom = self.atoms[start]
            end_atom = self.atoms[end]

            bond = Bond(self.options, start_atom, end_atom, bond_type, stereo)

            # we store both orientations of the bond, since we don't know yet
            # which way it will be used
            bonds[(start, end)] = bond
            bonds[(end, start)] = bond.invert()

            atom_pairs.append((start, end))

        return bonds, atom_pairs


    def parseTree(self, start_atom, end_atom):
        '''
        recurse over atoms in molecule to create a tree of bonds
        '''
        end_idx = end_atom.idx

        if start_atom is None: # this is the first atom in the molecule
            bond = DummyFirstBond(self.options, end_atom=end_atom)

        else:
            start_idx = start_atom.idx

            # guard against reentrant bonds. Can those even still happen?
            # apparently they can, even if I don't really understand how.
            if (start_idx, end_idx) in self.seen_bonds \
                                 or (end_idx, start_idx) in self.seen_bonds:
                return None

            # if we get here, the bond is not in the tree yet
            bond = self.bonds[(start_idx, end_idx)]

            # flag it as known
            self.seen_bonds.add((start_idx, end_idx))

            # detect bonds that close rings, and tell them render
            # with phantom atoms
            if end_idx in self.seen_atoms:
                bond.to_phantom = True
                return bond

        # flag end atom as known
        self.seen_atoms.add(end_idx)

        if end_atom is self.exit_atom:
            self.exit_bond = bond

        # recurse over the neighbors of the end atom
        for ni in end_atom.neighbors:
            if start_atom and ni == start_idx:  # don't recurse backwards
                continue

            next_atom = self.atoms[ni]
            next_bond = self.parseTree(end_atom, next_atom)

            if next_bond is not None:
                next_bond.parent = bond
                bond.descendants.append(next_bond)

        return bond


    def _getBond(self, tkbond):
        '''
        helper for aromatizeRing: find bond in parse tree that
        corresponds to toolkit bond
        '''
        start_idx = tkbond.source().index()
        end_idx = tkbond.destination().index()

        if (start_idx, end_idx) in self.seen_bonds:
            return self.bonds[(start_idx, end_idx)]

        # the bond must be going the other way ...
        return self.bonds[(end_idx, start_idx)]


    def aromatizeRing(self, ring, center_x, center_y):
        '''
        render a ring that is aromatic and is a regular polygon
        '''
        # first, set all bonds to aromatic
        ringbonds = list(ring.iterateBonds())

        for tkbond in ringbonds:
            bond = self._getBond(tkbond)
            bond.bond_type = 'aromatic'

        # any bond can serve as the anchor for the circle,
        # so we'll just use the last one from the loop
        atom = bond.end_atom

        outer_r, angle = compare_positions(atom.x, atom.y, center_x, center_y)
        # angle is based on raw coordinates - adjust for user-set rotation
        angle += self.options['rotate']

        # outer_r calculated from raw coordinates, must be adjusted
        # for bond scaling that may have taken place
        outer_r *= self.bond_scale

        alpha = ( math.pi / 2 - math.pi / len(ringbonds) )
        inner_r = math.sin(alpha) * outer_r

        arb = AromaticRingBond(self.options, bond, angle, outer_r, inner_r)
        bond.descendants.append(arb)


    def annotateRing(self, ring, is_aromatic):
        '''
        determine center, symmetry and aromatic character of ring
        I wonder if indigo would tell us directly about these ...

        annotate double bonds in rings, or alternatively decorate
        ring with aromatic circle.
        '''
        atoms = set()
        bond_lengths = []
        bonds = []


        for tkbond in ring.iterateBonds():
            bond = self._getBond(tkbond)
            bonds.append(bond)

            atoms.add(self.atoms[bond.start_atom.idx])
            atoms.add(self.atoms[bond.end_atom.idx])
            bond_lengths.append(bond.length)

        if len(bonds) > 8:  # large rings may foul things up, so we skip them.
            return

        bl_max = max(bond_lengths)
        bl_spread = (bl_max - min(bond_lengths)) / bl_max

        # determine ring center
        center_x = sum([atom.x for atom in atoms]) / len(atoms)
        center_y = sum([atom.y for atom in atoms]) / len(atoms)

        # compare distances from center. Also remember atoms and bond
        # angles; if the ring ends up being aromatized, we flag those
        # angles as occupied (by the fancy circle inside the ring).
        atom_angles = []
        center_distances = []

        for atom in atoms:
            length, angle = compare_positions(atom.x, atom.y, center_x, center_y)
            center_distances.append(length)
            atom_angles.append((atom, angle))

        cd_max = max(center_distances)
        cd_spread = (cd_max - min(center_distances)) / cd_max

        tolerance = 0.05
        is_symmetric = (cd_spread <= tolerance and bl_spread <= tolerance)

        if is_aromatic and is_symmetric and self.options['aromatic_circles']:
            # ring meets all requirements to be displayed with circle inside
            self.aromatizeRing(ring, center_x, center_y)
            # flag bond angles as occupied
            for atom, angle in atom_angles:
                atom.bond_angles.append(angle)

        else:   # flag orientation individual bonds - will influence
                # rendering of double bonds
            for bond in bonds:
                bond.is_clockwise(center_x, center_y)


    def annotateRings(self):
        '''
        modify double bonds in rings. In aromatic rings, we optionally
        do away with double bonds altogether and draw a circle instead
        '''
        self.tkmol.aromatize()

        all_rings = []

        for ring in self.tkmol.iterateSSSR():
            # bond-order == 4 means "aromatic"; all rings bonds must be aromatic
            is_aromatic = all(bond.bondOrder() == 4 for bond in ring.iterateBonds())
            all_rings.append((is_aromatic, ring))

        # prefer aromatic rings to nonaromatic ones, so that double bonds on
        # fused rings go preferably into aromatic rings
        all_rings.sort(key=lambda t:t[0])

        for is_aromatic, ring in reversed(all_rings):
            self.annotateRing(ring, is_aromatic)


    def scaleBonds(self):
        '''
        scale bonds according to user options
        '''
        if self.options['bond_scale'] == 'keep':
            pass

        elif self.options['bond_scale'] == 'normalize':
            lengths = [bond.length for bond in self.treebonds()]
            lengths = [round(l, self.options['bond_round']) for l in lengths]
            lengths = Counter(lengths)
            self.bond_scale = self.options['bond_stretch'] / lengths.most_common()

        elif self.options['bond_scale'] == 'scale':
            self.bond_scale = self.options['bond_stretch']

        for bond in self.treebonds():
            bond.length = self.bond_scale * bond.length


    def render(self):
        '''
        render molecule to chemfig
        '''
        output = []
        self._render(output, bond=self.root, level=0)

        return output


    def render_user(self):
        '''
        returns code formatted according to user options
        '''
        return cfm.format_output(self.options, self._rendered)


    def render_server(self):
        '''
        returns code formatted for server-side PDF generation
        '''
        # override some options
        params = dict(self.options)
        params['submol_name'] = None
        # params['terse'] = False  # why?
        params['chemfig_command'] = True

        return cfm.format_output(params, self._rendered)


    def _renderBranches(self, output, level, bonds):
        '''
        render a list of branching bonds indented and inside enclosing brackets.
        '''
        branch_indent = self.options['indent']

        for bond in bonds:
            output.append("(".rjust(level * branch_indent + cfm.BOND_CODE_WIDTH))
            self._render(output, bond, level)
            output.append(")".rjust(level * branch_indent + cfm.BOND_CODE_WIDTH))


    def _render(self, output, bond, level):
        '''
        recursively render the molecule.
        '''
        output.append(bond.render(level))
        branches = bond.descendants

        if bond is self.exit_bond: # wrap all downstream bonds in branch
            self._renderBranches(output, level+1, branches)

        elif branches: # prioritize bonds on the trunk from entry to exit
            for i, branch in enumerate(branches):
                if branch.is_trunk:
                    first = branches.pop(i)
                    break
            else:
                first = branches.pop(0)

            self._renderBranches(output, level+1, branches)
            self._render(output, first, level)


    def dimensions(self):
        '''
        this calculates the approximate width and height
        of the rendered molecule, in units of chemfig
        standard bond length (multiply with chemfig's
        \setatomsep parameter to obtain the physical size).

        It is only used for server side PDF generation,
        but maybe someone will have another use for it.
        '''
        minx = maxx = miny = maxy = None

        alpha = self.options['rotate']
        alpha *= math.pi/180

        sinalpha = math.sin(alpha)
        cosalpha = math.cos(alpha)

        for atom in list(self.atoms.values()):
            x, y = atom.x, atom.y

            xt = x * cosalpha - y * sinalpha
            yt = x * sinalpha + y * cosalpha

            if minx is None or xt < minx:
                minx = xt
            if maxx is None or xt > maxx:
                maxx = xt
            if miny is None or yt < miny:
                miny = yt
            if maxy is None or yt > maxy:
                maxy = yt

        xsize = (maxx - minx) * self.bond_scale
        ysize = (maxy - miny) * self.bond_scale

        return xsize, ysize


