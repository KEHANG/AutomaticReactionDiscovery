#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Contains the :class:`TSSearch` for finding transition states and reaction paths
using FSM/GSM.
"""

import pybel

import os
import logging
import time

from quantum import Gaussian, NWChem, QChem, QuantumError
from sm import FSM
import gen3D

###############################################################################

class TSError(Exception):
    """
    An exception class for errors that occur during a TS search.
    """
    pass

###############################################################################

class TSSearch(object):
    """
    Transition state finding using FSM/GSM with subsequent exact TS search and
    verification of the reaction path by intrinsic reaction coordinate
    calculation.
    The attributes are:

    ============== ====================== =====================================
    Attribute      Type                   Description
    ============== ====================== =====================================
    `reactant`     :class:`node.Node`     A node object containing the coordinates and atoms of the reactant molecule
    `product`      :class:`node.Node`     A node object containing the coordinates and atoms of the product molecule
    `ts`           :class:`node.Node`     The exact transition state
    `irc`          ``list``               The IRC path corresponding to the transition state
    `output_dir`   ``str``                The path to the output directory
    `Qclass`       ``class``              A class representing the quantum software
    `kwargs`       ``dict``               Options for FSM/GSM and quantum calculations
    `ngrad`        ``int``                The total number of gradient evaluations
    ============== ====================== =====================================

    """

    def __init__(self, reactant, product, **kwargs):
        if reactant.atoms != product.atoms:
            raise Exception('Atom labels of reactant and product do not match')
        self.reactant = reactant
        self.product = product

        self.output_dir = kwargs.get('output_dir', '')
        qprog = kwargs.get('qprog', 'gau')
        if qprog == 'gau':
            self.Qclass = Gaussian
        elif qprog == 'nwchem':
            self.Qclass = NWChem
        elif qprog == 'qchem':
            self.Qclass = QChem
        else:
            raise Exception('Invalid quantum software')
        self.kwargs = kwargs

        self.ts = None
        self.irc = None
        self.ngrad = None

    def initialize(self):
        """
        Initialize the TS search job.
        """
        logging.info('\nTS search initiated on ' + time.asctime() + '\n')
        self.logHeader()
        self.ngrad = 0

    def execute(self):
        """
        Run the string method, exact transition state search, IRC calculation,
        and check the results. The highest energy node is selected for the
        exact transition state search.
        """
        start_time = time.time()
        self.initialize()
        # self.genNew3D()  # temporary
        if 'theory_preopt' in self.kwargs:
            self.preoptimize()
        sm_path = self.executeStringMethod()

        energy_max = sm_path[0].energy
        for node in sm_path[1:-1]:
            if node.energy > energy_max:
                self.ts = node
                energy_max = node.energy

        self.executeExactTSSearch()
        self.executeIRC()
        self.checkResults()
        self.finalize(start_time)

    def finalize(self, start_time):
        """
        Finalize the job.
        """
        logging.info('\nTS search terminated on ' + time.asctime())
        logging.info('Total TS search run time: {0:.1f} s'.format(time.time() - start_time))
        logging.info(
            'Total number of gradient evaluations (excluding pre-optimization and IRC): {0}'.format(self.ngrad)
        )

    # def genNew3D(self):
    #     reactant = gen3D.readstring('xyz', self.reactant.getXYZ())
    #     product = gen3D.readstring('xyz', self.product.getXYZ())
    #     reactant.gen3D()
    #     product.gen3D()
    #     self.reactant = reactant.toNode()
    #     self.product = product.toNode()

    def preoptimize(self, reactant=False):
        """
        Optimize the reactant (if `reactant` is set to `True`) and product
        geometries.
        """
        logging.info('\n----------------------------------------------------------------------')
        logging.info('Pre-optimization initiated on ' + time.asctime() + '\n')
        start_time = time.time()

        kwargs = self.kwargs.copy()
        kwargs['theory'] = kwargs['theory_preopt']

        if reactant:
            try:
                self.reactant.optimizeGeometry(self.Qclass, name='preopt_reac', **kwargs)
            except QuantumError as e:
                logging.error('Pre-optimization of reactant structure was unsuccessful')
                logging.info('Error message: {0}'.format(e))
            else:
                logging.info('Optimized reactant structure:\n' + str(self.reactant))
                logging.info('Energy ({0}) = {1}'.format(kwargs['theory'], self.reactant.energy))
        try:
            self.product.optimizeGeometry(self.Qclass, name='preopt_prod', **kwargs)
        except QuantumError as e:
            logging.error('Pre-optimization of product structure was unsuccessful')
            logging.info('Error message: {0}'.format(e))
        else:
            logging.info('Optimized product structure:\n' + str(self.product))
            logging.info('Energy ({0}) = {1}'.format(kwargs['theory'], self.product.energy))

        logging.info('\nPre-optimization terminated on ' + time.asctime())
        logging.info('Total pre-optimization run time: {0:.1f} s'.format(time.time() - start_time))
        logging.info('----------------------------------------------------------------------\n')

    def executeStringMethod(self):
        """
        Run the string method with the options specified in `kwargs`. Return
        the string.
        """
        fsm = FSM(self.reactant, self.product, **self.kwargs)
        fsmpath = fsm.execute()
        self.ngrad += fsm.ngrad
        return fsmpath

    def executeExactTSSearch(self):
        """
        Run the exact transition state search and update `self.ts`.
        """
        logging.info('\n----------------------------------------------------------------------')
        logging.info('Exact TS search initiated on ' + time.asctime() + '\n')
        start_time = time.time()
        logging.info('Initial TS structure:\n' + str(self.ts) + '\nEnergy = ' + str(self.ts.energy))

        try:
            ngrad = self.ts.optimizeGeometry(self.Qclass, ts=True, name='TSopt', **self.kwargs)
        except QuantumError as e:
            logging.error('Exact TS search did not succeed and terminated with the message: {0}'.format(e))
            raise TSError('TS search failed during exact TS search')

        with open(os.path.join(self.output_dir, 'ts.out'), 'w') as f:
            f.write('Transition state:\n')
            f.write('Energy = ' + str(self.ts.energy) + '\n')
            f.write(str(self.ts) + '\n')

        logging.info('Optimized TS structure:\n' + str(self.ts) + '\nEnergy = ' + str(self.ts.energy))
        logging.info('Number of gradient evaluations during exact TS search: {0}'.format(ngrad))
        logging.info('\nExact TS search terminated successfully on ' + time.asctime())
        logging.info('Total exact TS search run time: {0:.1f} s'.format(time.time() - start_time))
        logging.info('----------------------------------------------------------------------\n')
        self.ngrad += ngrad

    def executeIRC(self):
        """
        Run an IRC calculation using the exact TS geometry and save the path to
        `self.irc`.
        """
        logging.info('\n----------------------------------------------------------------------')
        logging.info('IRC calculation initiated on ' + time.asctime() + '\n')
        start_time = time.time()

        try:
            self.irc = self.ts.getIRCpath(self.Qclass, name='IRC', **self.kwargs)
        except QuantumError as e:
            logging.error('IRC calculation did not succeed and terminated with the message: {0}'.format(e))
            raise TSError('TS search failed during IRC calculation')

        with open(os.path.join(self.output_dir, 'irc.out'), 'w') as f:
            for node_num, node in enumerate(self.irc):
                f.write('Node ' + str(node_num + 1) + ':\n')
                f.write('Energy = ' + str(node.energy) + '\n')
                f.write(str(node) + '\n')

        logging.info('IRC path endpoints:\n' + str(self.irc[0]) + '\n****\n' + str(self.irc[-1]))
        logging.info('\nIRC calculation terminated successfully on ' + time.asctime())
        logging.info('Total IRC calculation run time: {0:.1f} s'.format(time.time() - start_time))
        logging.info('----------------------------------------------------------------------\n')

    def checkResults(self):
        """
        Compare the IRC path endpoints to the reactant and product structures.
        If they represent the same molecules, then the TS search was
        successful. If not, notify the user to check the results manually.
        """
        logging.info('\n----------------------------------------------------------------------')
        logging.info('Begin IRC endpoint check...')
        reactant = pybel.readstring('xyz', self.reactant.getXYZ())
        product = pybel.readstring('xyz', self.product.getXYZ())
        irc_end_1 = pybel.readstring('xyz', self.irc[0].getXYZ())
        irc_end_2 = pybel.readstring('xyz', self.irc[-1].getXYZ())
        reactant_smi = reactant.write('can').strip()
        product_smi = product.write('can').strip()
        irc_end_1_smi = irc_end_1.write('can').strip()
        irc_end_2_smi = irc_end_2.write('can').strip()

        check1, check2 = False, False
        if reactant_smi == irc_end_1_smi or reactant_smi == irc_end_2_smi:
            check1 = True
        if product_smi == irc_end_1_smi or product_smi == irc_end_2_smi:
            check2 = True

        if check1 and check2:
            logging.info('IRC check was successful. The IRC path endpoints correspond to the reactant and product.')
        else:
            logging.warning('IRC check was unsuccessful. Check the results manually.')
        logging.info('Coordinates converted to SMILES:')
        logging.info('Reactant: {0}\nProduct: {1}\nIRC endpoint 1: {2}\nIRC endpoint 2: {3}'.
                     format(reactant_smi, product_smi, irc_end_1_smi, irc_end_2_smi))
        logging.info('----------------------------------------------------------------------\n')

    def logHeader(self):
        """
        Output a log file header containing identifying information about the
        TS search.
        """
        logging.info('#######################################################################')
        logging.info('############################## TS SEARCH ##############################')
        logging.info('#######################################################################')
        logging.info('Reactant structure:\n' + str(self.reactant))
        logging.info('Product structure:\n' + str(self.product))
        logging.info('#######################################################################\n')
