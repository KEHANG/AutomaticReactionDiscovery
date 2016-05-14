#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Contains the :class:`Node` for working with three-dimensional representations
of molecules in Cartesian coordinates and evaluating energies and gradients
using quantum chemical calculations.
"""

import numpy as np

from sys import platform as _platform
import subprocess
import os
import warnings

import gaussian

###############################################################################

class Node(object):
    """
    Three-dimensional representation of a molecular configuration.
    The attributes are:

    =============== ======================= ===================================
    Attribute       Type                    Description
    =============== ======================= ===================================
    `coordinates`   :class:`numpy.ndarray`  A 3N x 1 array containing the 3D coordinates of each atom (in Angstrom)
    `number`        :class:`list`           A list of length N containing the integer atomic number of each atom
    `multiplicity`  ``int``                 The multiplicity of this species, multiplicity = 2*total_spin+1
    =============== ======================= ===================================

    N is the total number of atoms in the molecule. The integer index of each
    atom corresponds to three subsequent entries in the coordinates vector,
    which represent the set of x, y, and z coordinates of the atom.
    """

    # Dictionary of elements corresponding to atomic numbers
    elements = {1: 'H', 5: 'B', 6: 'C', 7: 'N', 8: 'O', 9: 'F', 15: 'P', 16: 'S', 17: 'Cl', 35: 'Br', 53: 'I'}

    def __init__(self, coordinates, number, multiplicity=1):
        self.coordinates = np.array(coordinates).flatten()
        self.number = [int(round(num, 0)) for num in number]
        assert len(self.coordinates) == 3 * len(number)
        self.multiplicity = multiplicity

    def __str__(self):
        """
        Return a human readable string representation of the object
        """
        return_string = ''
        coord_array = self.coordinates.reshape(len(self.number), 3)
        for atom_num, atom in enumerate(coord_array):
            coords = [str(coord) for coord in atom]
            return_string += self.elements[self.number[atom_num]] + '    ' + '  '.join(coords) + '\n'
        return return_string[:-1]

    def getTangent(self, other):
        """
        Calculate and return tangent direction between two nodes based on LST
        path between the nodes. The tangent vector points from `self` to
        `other`, which are both of type :class:`node.Node`.
        """
        assert len(self.coordinates) == len(other.coordinates)
        dist = other.coordinates - self.coordinates
        return dist / np.linalg.norm(dist)

    def executeGaussianJob(self, name='forceJob', jobtype='force', ver='g09', level_of_theory='um062x/cc-pvtz',
                           nproc=32, mem='1500mb'):
        """
        Execute 'force' job type using the Gaussian software package. This
        method can only be run on a UNIX system where Gaussian is installed.
        Return filename of Gaussian logfile.
        """
        coord_array = self.coordinates.reshape(len(self.number), 3)

        # Create Gaussian input file
        input_file = name + '.com'
        with open(input_file, 'w') as f:
            f.write('%chk=' + name + '.chk\n')
            f.write('%mem=' + mem + '\n')
            f.write('%nprocshared=' + str(int(nproc)) + '\n')
            f.write('# ' + jobtype + ' ' + level_of_theory + '\n\n')
            f.write(name + '\n\n')
            f.write('0 ' + str(self.multiplicity) + '\n')

            for atom_num, atom in enumerate(coord_array):
                f.write(self.elements[self.number[atom_num]] + '                 ')
                for coord in atom:
                    f.write(str(coord) + '    ')
                f.write('\n')

            f.write('\n')

        # Run job and wait until termination
        if _platform == 'linux' or _platform == 'linux2':
            output_file = name + '.log'
            subprocess.Popen([ver, input_file, output_file]).wait()
            os.remove(input_file)
            os.remove(name + '.chk')
        else:
            os.remove(input_file)
            raise OSError('Invalid operating system')

        # Check if job completed or if it terminated with an error
        if os.path.isfile(output_file):
            completed = False
            with open(output_file, 'r') as f:
                gaussian_output = f.readlines()
            for line in gaussian_output:
                if 'Error termination' in line:
                    raise gaussian.GaussianError('Force job terminated with an error')
                elif 'Normal termination' in line:
                    completed = True
            if not completed:
                raise gaussian.GaussianError('Force job did not terminate')
            else:
                return output_file
        else:
            raise IOError('Gaussian output file could not be found')

    def perpOpt(self, other, nsteps=4, min_desired_energy_change=2.5, line_search_factor = 0.7,
                gaussian_ver='g09', level_of_theory='um062x/cc-pvtz', nproc=32, mem='1500mb'):
        """
        Optimize node in direction of negative perpendicular gradient using the
        Newton-Raphson method with a BFGS Hessian update scheme. Requires input
        of closest node at the other end of the string so that the appropriate
        tangent vector and perpendicular gradient can be calculated.

        Returns the energy of the optimized node.

        Set `min_desired_energy_change` to the energy difference between
        reactant and product if the difference is less than 2.5 kcal/mol.
        """
        if not isinstance(nsteps, int):
            raise TypeError('nsteps has to be an integer value')
        identity_mat = np.eye(len(self.coordinates))
        tangent = self.getTangent(other)
        hessian_inv = identity_mat
        min_desired_energy_change /= 627.5095

        # Calculate gradient and energy
        logfile = self.executeGaussianJob('step_1', 'force', gaussian_ver, level_of_theory, nproc, mem)
        grad = gaussian.getGradient(logfile)
        print 'Gradient:'
        print grad
        print '\n'
        energy = gaussian.getEnergy(logfile)
        print 'Energy:'
        print energy
        print '\n'
        energy_old = energy
        os.remove(logfile)

        # Calculate perpendicular gradient
        perp_grad = (identity_mat - np.outer(tangent, tangent)).dot(grad)
        print 'Perpendicular gradient:'
        print perp_grad
        print '\n'

        # Create empty arrays for storing old values
        coordinates_old = np.empty_like(self.coordinates)
        perp_grad_old = np.empty_like(perp_grad)
        hessian_inv_old = np.empty_like(hessian_inv)

        k = 1
        unstable = False
        while k <= nsteps:
            # Calculate search direction
            direction = hessian_inv.dot(perp_grad)
            direction_norm = np.linalg.norm(direction)
            search_dir = - direction / direction_norm
            print 'Search direction:'
            print search_dir
            print '\n'

            # Calculate scaling factor
            scale_factor_max = 0.05 / np.absolute(search_dir).max()
            scale_factor_min = 1.0 - line_search_factor * direction_norm
            desired_energy_change = max(abs(energy - energy_old), min_desired_energy_change)
            line_search_term = perp_grad.dot(search_dir)
            scale_factor = - 2.0 * desired_energy_change / line_search_term
            if scale_factor < scale_factor_min < scale_factor_max:
                scale_factor = scale_factor_min
            elif scale_factor > scale_factor_max:
                scale_factor = scale_factor_max
            if scale_factor < 0.0:
                # Terminate if resetting Hessian did not resolve instability
                if unstable:
                    warnings.warn('Optimization terminated due to unstable scaling factor')
                    break
                unstable = True
                hessian_inv = identity_mat
                print 'Skipped remainder of loop.\n'
                continue
            unstable = False
            print 'Scaling factor:'
            print scale_factor
            print '\n'

            # Update
            np.copyto(coordinates_old, self.coordinates)
            print 'Old coordinates:'
            print coordinates_old
            print '\n'
            np.copyto(perp_grad_old, perp_grad)
            np.copyto(hessian_inv_old, hessian_inv)
            energy_old = energy

            # Take minimization step
            self.coordinates += scale_factor * search_dir
            print 'Coordinates:'
            print self.coordinates
            print '\n'

            # Terminate after maximum number of gradient calls
            if k == nsteps:
                logfile = self.executeGaussianJob('final_energy', 'sp', gaussian_ver, level_of_theory, nproc, mem)
                energy = gaussian.getEnergy(logfile)
                break

            # Calculate new gradient and energy
            name = 'step_' + str(k+1)
            logfile = self.executeGaussianJob(name, 'force', gaussian_ver, level_of_theory, nproc, mem)
            grad = gaussian.getGradient(logfile)
            print 'Gradient:'
            print grad
            print '\n'
            energy = gaussian.getEnergy(logfile)
            print 'Energy:'
            print energy
            print '\n'
            os.remove(logfile)

            # Calculate perpendicular gradient
            perp_grad = (identity_mat - np.outer(tangent, tangent)).dot(grad)
            print 'Perpendicular gradient:'
            print perp_grad
            print '\n'

            # Check remaining termination conditions
            energy_change = abs(energy - energy_old)
            if (energy_change < 0.5 / 627.5095 or
                    abs(perp_grad.dot(search_dir)) <= - line_search_factor * line_search_term):
                break

            # Update inverse Hessian
            perp_grad_change = perp_grad - perp_grad_old
            print 'Coordinates:'
            print self.coordinates
            print 'Old coordinates:'
            print coordinates_old
            print '\n'
            step = self.coordinates - coordinates_old
            denom = step.dot(perp_grad_change)
            print 'Hessian inverse:'
            print hessian_inv_old
            print 'Perpendicular gradient change:'
            print perp_grad_change
            print 'Step'
            print step
            print 'Denominator in gradient expression:'
            print denom
            hessian_inv += (1.0 + perp_grad_change.dot((hessian_inv_old.dot(perp_grad_change))) / denom) * \
                np.outer(step, step) / denom - (np.outer(step, perp_grad_change.dot(hessian_inv_old)) +
                                                np.outer(hessian_inv_old.dot(perp_grad_change), step)) / denom

            # Update counter
            k += 1

        return energy