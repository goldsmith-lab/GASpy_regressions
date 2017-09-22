'''
This class performs regressions and is then able to make predictions with the resulting
model. Note that you must specify general regressions settings and conditions for pulling
GASdb data to instantiate this class, and then call on any of the `fit_*` methods to
actually perform the regression on the features. Then you can use the `predict`
and `parity_plot` methods.
'''
# pylint: disable=attribute-defined-outside-init

__author__ = 'Kevin Tran'
__email__ = 'ktran@andrew.cmu.edu'

import pdb
from pprint import pprint
import itertools
import copy
import sys
import math
import warnings
import numpy as np
import dill as pickle
pickle.settings['recurse'] = True     # required to pickle lambdify functions (for alamopy)
from sklearn import metrics
from sklearn.model_selection import train_test_split
from plotly.offline import init_notebook_mode, plot, iplot
init_notebook_mode(connected=True)
#import plotly.plotly as py
import plotly.graph_objs as go
from preprocessor import GASpyPreprocessor
sys.path.append('..')
from gaspy import utils


class GASpyRegressor(object):
    '''
    All of the `fit_*` methods have similar output structures. Here it is:

    Outputs:
        rmses       A nested dictionary whose first set of keys are the block ('no_block' if there
                    is no blocking). The second set of keys are the dataset (i.e., 'train',
                    'test', or 'train+test'. The values of the sub-dictionary are the
                    root-mean-squared-error of the model for the corresponding block
                    and dataset.
        errors      The same as `rmses`, but it returns an np.array of errors instead of a
                    float of RMSE values.
    Resulting attributes:
        model_name  A string indicating the name of the method used. This is useful for
                    labeling the plots in the `parity_plot` method.
        rmses       A nested dictionary with the following structure:
                    rmses = {'block': {'train': RMSE,
                                       'test': RMSE,
                                       'train+test': RMSE}}
        errors      The same thing as `rmses`, but instead of RMSE values, they're
                    np.arrays of the errors
        _predict    A function that can turn turn a preprocessed input into a prediction.
                    Inputs:
                        inputs  A numpy array. 1st dimension shows different data points,
                                while the second dimension shows different numerical features
                        block   A tuple indicating the blocks of the model that you want to use
                    Output:
                        predictions A numpy array. 1st dimension shows different data points,
                                    while the second dimension shows different responses.
    '''
    # pylint: disable=too-many-instance-attributes
    def __init__(self, features, responses, blocks=None,
                 fingerprints=None, vasp_settings=None,
                 collection='adsorption', energy_min=-4, energy_max=4, f_max=0.5,
                 ads_move_max=1.5, bare_slab_move_max=0.5, slab_move_max=1.5,
                 train_size=0.75, random_state=42):
        # pylint: disable=too-many-branches, too-many-arguments, too-many-statements
        '''
        Pull and preprocess the data that you want to regress. The "regression options"
        define how we want to perform the regression. "Pulling/filtering" options decide
        where we want to pull the data, what we want to pull, and what things we do not
        want to pull.

        Inputs (regression options):
            features            A list of strings for each of the features that you want
                                to include.  These strings should correspond to the
                                1st-level hidden methods in `GASpyPreprocessor`, but
                                without the leading underscore.  For example:
                                features = ('coordcount', 'ads')
            responses           A list of strings for each of the responses that you
                                want to include.  Pretty much just like features.
            blocks              A list of strings for each of the fingerprints on which
                                the user wants to block
        Inputs (pulling/filtering options):
            fingerprints        Mongo queries of parameters that you want pulled.
                                Note that we automatically set some of these queries
                                based on the features and responses you are trying to use.
                                So you only need to define mongo queries for any extra
                                information you want.
            vasp_settings       A string of vasp settings. Use the
                                vasp_settings_to_str function in GAspy
            collection          A string for the mongo db collection you want to pull from.
            energy_min          The minimum adsorption energy to pull from the
                                adsorption DB (eV)
            energy_max          The maximum adsorption energy to pull from the
                                adsorption DB (eV)
            ads_move_max        The maximum distance that an adsorbate atom may
                                move (angstrom)
            bare_slab_move_max  The maxmimum distance that a slab atom may move
                                when it is relaxed without an adsorbate (angstrom)
            slab_move_max       The maximum distance that a slab atom may move
                                (angstrom)
            f_max               The upper limit on the maximum force on an atom
                                in the system
        Resulting attributes:
            features    Same thing as the input. Used mainly for making file_name to save
            responses   Same thing as the input. Used mainly for making file_name to save
            blocks      Same thing as the input. Used mainly for making file_name to save
            x           A nested dictionary with the following structure:
                        x = {'block': {'train': np.array(INPUT_DATA),
                                       'test': np.array(INPUT_DATA),
                                       'train+test': np.array(INPUT_DATA)}
            y           The same as `x`, but for the outputs, not the inputs
            p_docs      The same as `x`, but the dict values are not np.arrays of data.
                        Instead, they are dictionaries with structures analogous to the
                        `p_docs` returned by `gaspy.utils.get_docs`, i.e., it is a dictionary
                        whose keys are the keys of `fingerprints` and whose values are lists
                        of the results.
            pp          The instance of GASpyPreprocessor that was used to preprocess the
                        data set pulled by FeaturePuller. This is used to preprocess other data
                        to make future predictions.
            block_list  A list of tuples. Each tuple represents a different block, and
                        each element in these tuples corresponds to a different fingerprint
                        value. For example:  Say that `blocks` = ['adsorbate`, `mpid`] and
                        'adsorbate` could take values of ['H', 'O'], while 'mpid' could
                        take values of ['mp-23', 'mp-126']. Then `block_list` would be
                        [('H', 'mp-23'), ('O', 'mp-23'), ('H', 'mp-126'), ('O', 'mp-126')].
                        Note that the order of the values in the tuple corresponds to the
                        order in which the fingerprints are listed within `blocks`.
                        If there is no block, then `block_list` = 'no_block'.
        '''
        self.features = features
        self.responses = responses
        self.blocks = blocks

        # Python doesn't like dictionaries being used as default values, so we initialize here
        if not vasp_settings:
            vasp_setings = utils.vasp_settings_to_str({})
        if not fingerprints:
            fingerprints = {}

        # Make sure that we are always pulling out/storing the mongo ID number
        fingerprints['mongo_id'] = '$_id'
        # Some features require specific fingerprints. Here, we make sure that those
        # fingerprints are included
        _features = dict.fromkeys(features)
        if 'coordcount' in _features:
            fingerprints['coordination'] = '$processed_data.fp_final.coordination'
            fingerprints['symbols'] = '$atoms.chemical_symbols'
        if 'rnnc_count' in _features:
            fingerprints['coordination'] = '$processed_data.fp_final_coordination'
            fingerprints['symbols'] = '$atoms.chemical_symbols'
            fingerprints['nnc'] = '$processed_data.fp_init.nextnearestcoordination'
        if 'ads' in _features:
            fingerprints['adsorbates'] = '$processed_data.calculation_info.adsorbate_names'
        if 'hash' in _features:
            fingerprints['mpid'] = '$processed_data.calculation_info.mpid'
            fingerprints['miller'] = '$processed_data.calculation_info.miller'
            fingerprints['top'] = '$processed_data.calculation_info.top'
            fingerprints['coordination'] = '$processed_data.fp_final.coordination'
            fingerprints['nextnearestcoordination'] = '$processed_data.fp_init.nextnearestcoordination'
            fingerprints['neighborcoord'] = '$processed_data.fp_init.neighborcoord'
        # Some responses require specific queries. Here, we make sure that the correct
        # queries are defined
        _responses = dict.fromkeys(responses)
        if 'energy' in _responses:
            fingerprints['energy'] = '$results.energy'

        # Pull the data into parsed mongo documents (i.e., a dictionary of lists), `p_docs`
        with utils.get_adsorption_db() as client:
            _, p_docs = utils.get_docs(client, collection, fingerprints,
                                       adsorbates=None,
                                       calc_settings=None,
                                       vasp_settings=vasp_settings,
                                       energy_min=energy_min,
                                       energy_max=energy_max,
                                       f_max=f_max,
                                       ads_move_max=ads_move_max,
                                       bare_slab_move_max=bare_slab_move_max,
                                       slab_move_max=slab_move_max)
        if not p_docs.values()[0]:
            raise Exception('Failed to find any data. Please check your query settings.')

        # Preprocess the features
        pp = GASpyPreprocessor(p_docs, features)
        x = pp.transform(p_docs)
        # Pull out, stack (if necessary), and numpy-array-ify the responses.
        # We might do real preprocessing to these one day. But not today.
        if len(responses) == 1:
            y = np.array(p_docs[responses[0]])
        elif len(responses) > 1:
            y = []  # pylint: disable=redefined-variable-type
            for response in responses:
                y.append(np.array(p_docs[response]))
            y = np.concatenate(tuple(y), axis=1)

        # Split the inputs and outputs. We also pull out indices for splitting so
        # that we can split `p_docs`
        x_train, x_test, y_train, y_test, indices_train, indices_test = \
                train_test_split(x, y, range(len(x)),
                                 train_size=train_size,
                                 random_state=random_state)
        p_docs_train = {fp: np.array(values)[indices_train] for fp, values in p_docs.iteritems()}
        p_docs_test = {fp: np.array(values)[indices_test] for fp, values in p_docs.iteritems()}

        # Assign the information to the class attributes
        self.x = {'no_block': {'train': x_train,
                               'test': x_test,
                               'train+test': x}}
        self.y = {'no_block': {'train': y_train,
                               'test': y_test,
                               'train+test': y}}
        self.p_docs = {'no_block': {'train': p_docs_train,
                                    'test': p_docs_test,
                                    'train+test': p_docs}}

        if blocks:
            # If we want to block by some fingerprint, then we had better pull it out.
            # Here are some common ones to make life easy.
            _blocks = dict.fromkeys(blocks)
            if 'adsorbate' in _blocks:
                fingerprints['adsorbates'] = '$processed_data.calculation_info.adsorbate_names'
            if 'mpid' in _blocks:
                fingerprints['mpid'] = '$processed_data.calculation_info.mpid'
            if 'miller' in _blocks:
                fingerprints['miller'] = '$processed_data.calculation_info.miller'
            if 'top' in _blocks:
                fingerprints['top'] = '$processed_data.calculation_info.top'
            if 'coordination' in _blocks:
                fingerprints['coordination'] = '$processed_data.fp_final.coordination'
            if 'nextnearestcoordination' in _blocks:
                fingerprints['nextnearestcoordination'] = '$processed_data.fp_init.nextnearestcoordination'
            if 'neighborcoord' in _blocks:
                fingerprints['neighborcoord'] = '$processed_data.fp_init.neighborcoord'

            # TODO:  Address this when we start doing co-adsorption.
            # If we're blocking by adsorbate, then we create a new fingerprint, `adsorbate`,
            # from the fingerprint `adsorbates`. Note that the latter is a list of adsorbates,
            # while the former is simply the first adsorbate. This really only works
            # because we're only looking at one adsorbate at a time right now.
            if 'adsorbate' in dict.fromkeys(blocks):
                for dataset, p_docs in self.p_docs.iteritems():
                    self.p_docs[dataset] = [adsorbates[0]
                                            for adsorbates in p_docs[dataset]['adsorbates']]

            # Warn the user if they're trying to block by something that they might not
            # be pulling
            for block in blocks:
                if block not in fingerprints:
                    warnings.warn('You are trying to block by %s, but we did not find that fingerprint'
                                  % block)

            # `block_values` is a list of sublists, where each sublist contains all of the unique
            # values for each of the fingerprints specified in `blocks`. The order of the sublists
            # corresponds to the order of the fingerprints in the `blocks` list. For example,
            # if blocks = ['adsorbate', 'top'], then block_values could be
            # [['O', 'CO'], ['Top', 'Bottom']]. We use block_values to create `block_list`.
            block_values = []
            for block in blocks:
                block_values.append(np.unique(self.p_docs['no_block']['train+test'][block]).tolist())   # pylint: disable=E1101
            self.block_list = [block for block in itertools.product(*block_values)]
            # Filter the class attributes for each block, and then add the filtered
            # data to the attributes as sub-dictionaries
            for block in self.block_list:
                self.x[block] = self._filter(self.x['no_block'], blocks, block)
                self.y[block] = self._filter(self.y['no_block'], blocks, block)
                self.p_docs[block] = self._filter(self.p_docs['no_block'], blocks, block)

        # If there is no blocking, then set `block_list` to ['no_block'], which will cause this
        # class' methods to act on the entire dataset pulled by `PullFeatures`.
        else:
            self.block_list = ['no_block']


    def _filter(self, data, blocks, block):
        '''
        Filter the `data` according to the `block` that it belongs to.
        Note that the algorithm to create the `fdata` intermediary object is... complicated.
        I hurt my brain writing it. Feel free to pick it apart to make it easier to read.

        Inputs:
            data        A dictionary whose keys are 'train+test', 'train', and 'test'.
                        The values are numpy arrays of data that are yielded by `PullFeatures`...
                        or they are dictionaries of parsed mongo data are also yielded by
                        `PullFeatures`
            blocks      A list of the names of the fingerprints that we are blocking on,
                        e.g., ['adsorbate', 'mpid']
            block       A tuple of the values of the fingerprints values that we are blocking on,
                        e.g., ('H', 'mp-126'). The order of the block values must
                        match the order of block names in the `block_names` list.
        Output:
            filtered_data   The subset of `data` whose fingerprint values match those supplied
                            in `block`
        '''
        # Initialize output
        filtered_data = dict.fromkeys(data)
        # Find the type of the values of `data` so that we treat it correctly
        dtype = type(data.values()[0])

        # If `_data` is an np.array, then treat it as such. This probably means
        # that `_data` is either `x` or `y`
        if dtype == type(np.array([])):
            for dataset, _data in data.iteritems():
                fdata = [datum for i, datum in enumerate(_data)
                         if all([fp_value == self.p_docs['no_block'][dataset][blocks[j]][i]
                                 for j, fp_value in enumerate(block)])]
                # Convert to np.array so that it can be accepted by most regressors
                filtered_data[dataset] = np.array(fdata)

        # If `_data` is a dict, then we need to loop through each element. This
        # probably means that `_data` is `p_docs`.
        elif dtype == dict:
            for dataset, _data in data.iteritems():
                filtered_data[dataset] = dict.fromkeys(_data)
                for p_doc_key, __data in _data.iteritems():
                    fdata = [datum for i, datum in enumerate(__data)
                             if all([fp_value == self.p_docs['no_block'][dataset][blocks[j]][i]
                                     for j, fp_value in enumerate(block)])]
                    filtered_data[dataset][p_doc_key] = fdata

        return filtered_data


    def fit_sk(self, regressor, x_dict=None, y_dict=None, blocks=None, model_name=None):
        '''
        This method will assume that the regressor object you are passing it is an SKLearn
        type object, and will thus have `fit` and `predict` methods.

        Inputs:
            regressor   An SKLearn-type regressor object (e.g., GaussianProcessRegressor)
            x_dict      The same thing as `self.x`, but the user may specify something
                        to use instead of `self.x`.
            y_dict      The same thing as `self.y`, but the user may specify something
                        to use instead of `self.y`.
            blocks      A list of tuples indicating the blocks that you want to perform
                        the regression on.
            model_name  If you want to name this model something differently, then go
                        ahead. Doing so might reduce regressor saving conflicts.
        '''
        # pylint: disable=too-many-arguments
        # Set defaults
        if not x_dict:
            x_dict = self.x
        if not y_dict:
            y_dict = self.y
        if not blocks:
            blocks = self.block_list
        if not model_name:
            model_name = 'sk'
        self.model_name = model_name

        # Initialize the outputs
        models = dict.fromkeys(blocks)
        rmses = dict.fromkeys(blocks)
        errors = dict.fromkeys(blocks)

        for block in blocks:
            # Initialize some more structuring for the outputs
            rmses[block] = dict.fromkeys(x_dict[block])
            errors[block] = dict.fromkeys(x_dict[block])
            # Copy the regressor template and perform the regression
            models[block] = copy.deepcopy(regressor)
            models[block].fit(x_dict[block]['train'], y_dict[block]['train'])

            # Post-process the results for each set of training, testing, and
            # train+test data
            for dataset, y in y_dict[block].iteritems():
                y_hat = models[block].predict(x_dict[block][dataset])
                mse = metrics.mean_squared_error(y, y_hat)
                rmses[block][dataset] = math.sqrt(mse)
                errors[block][dataset] = y - y_hat

        # Create the model
        def _predict(inputs, block='no_block'):
            model = models[block]
            predictions = model.predict(inputs)
            return predictions

        # Assign the attributes
        self._predict = _predict
        self.rmses = rmses
        self.errors = errors


    def fit_tpot(self, regressor, x_dict=None, y_dict=None, blocks=None, model_name=None):
        '''
        This method will assume that the regressor object you are passing it is a TPOT regressor
        type object, and will thus have `fit` and `predict` methods. And it will need
        to be modified in order for it to be pickled.

        Inputs:
            regressor   An TPOTRegressor object
            x_dict      The same thing as `self.x`, but the user may specify something
                        to use instead of `self.x`.
            y_dict      The same thing as `self.y`, but the user may specify something
                        to use instead of `self.y`.
            blocks      A list of tuples indicating the blocks that you want to perform
                        the regression on.
            model_name  If you want to name this model something differently, then go
                        ahead. Doing so might reduce regressor saving conflicts.
        '''
        # pylint: disable=too-many-arguments
        # Set defaults
        if not x_dict:
            x_dict = self.x
        if not y_dict:
            y_dict = self.y
        if not blocks:
            blocks = self.block_list
        if not model_name:
            model_name = 'tpot'
        self.model_name = model_name

        # Initialize the outputs
        models = dict.fromkeys(blocks)
        rmses = dict.fromkeys(blocks)
        errors = dict.fromkeys(blocks)

        for block in blocks:
            # Initialize some more structuring for the outputs
            rmses[block] = dict.fromkeys(x_dict[block])
            errors[block] = dict.fromkeys(x_dict[block])
            # Copy the regressor template and perform the regression
            models[block] = copy.deepcopy(regressor)
            models[block].fit(x_dict[block]['train'], y_dict[block]['train'])
            # Convert the TPOTRegressor into a pipeline, because we can't pickle
            # TPOTRegressors
            models[block] = models[block].fitted_pipeline_

            # Post-process the results for each set of training, testing, and
            # train+test data
            for dataset, y in y_dict[block].iteritems():
                y_hat = models[block].predict(x_dict[block][dataset])
                mse = metrics.mean_squared_error(y, y_hat)
                rmses[block][dataset] = math.sqrt(mse)
                errors[block][dataset] = y - y_hat

        # Create the model
        def _predict(inputs, block='no_block'):
            model = models[block]
            predictions = model.predict(inputs)
            return predictions

        # Assign the attributes
        self._predict = _predict
        self.rmses = rmses
        self.errors = errors


    # TODO:  Write this part. Try to figure out/remember how to get the lambda function
    # out of the alamopy output so that we can apply it to the other data sets
    def fit_alamo(self, x_dict=None, y_dict=None, blocks=None, **kwargs):
        '''
        Use alamopy to perform the regression.

        Inputs:
            x_dict  The same thing as `self.x`, but the user may specify something
                    to use instead of `self.x`.
            y_dict  The same thing as `self.y`, but the user may specify something
                    to use instead of `self.y`.
            blocks      A list of tuples indicating the blocks that you want to perform
                        the regression on.
            kwargs  The same key word arguments that you would pass to alamopy.doalamo,
                    excluding the training and testing data.
        '''
        self.model_name = 'alamo'
        # Set defaults
        if not x_dict:
            x_dict = self.x
        if not y_dict:
            y_dict = self.y
        if not blocks:
            blocks = self.block_list

        _predict = 'foo'
        rmses = 'foo'
        errors = 'bar'

        # Assign the attributes
        self._predict = _predict
        self.rmses = rmses
        self.errors = errors


    # TODO:  Finish updating this method.
    def fit_hierarchical(self, outer_models, outer_rmses, outer_error,
                         inner_feature_set, inner_method, inner_regressor,
                         blocks=None):
        # pylint: disable=too-many-arguments
        '''
        This method accepts the results of many of the other methods of this class and
        then tries to fit another model to regress the subsequent erros of the original
        model. Note that this method assumes that you use the same blocking structure
        for both the inner and the outer methods, and that you also use the same GASpy_DB
        snapshot.

        Inputs:
            outer_models        The `models` for the outer model
            outer_rmses         The `rmses` for the outer model
            outer_errors        The `errors` for the outer model
            inner_feature_set   A string corresponding to the feature set for the inner model
            inner_method        The `Regress` method to be used to create the inner model
            inner_regressor     The regressing object that should be used by the inner model
            blocks      A list of tuples indicating the blocks that you want to perform
                        the regression on.
        Outputs:
            models  A function that accepts the input to the outer model and the input
                    to the inner model to make a final prediction. All inputs should
                    probably be np.arrays. The outputs will probably np.arrays.
            rmses   This will be the same as "normal", but it will have two additional
                    keys:  'inner_model' and 'outer_model'. The subsequent values
                    will be identical to a normal `rmses` object, but specific to
                    either the inner or outer model.
            errors  Same as `rmses`, but for the errors instead
        '''
        # Set defaults
        if not blocks:
            blocks = self.block_list
        if not model_name:
            model_name = 'hierarchical'
        self.model_name = model_name
        # Initialize the outputs
        models = dict.fromkeys(blocks)
        rmses = dict.fromkeys(blocks)
        errors = dict.fromkeys(blocks)
        # Store the outer model information
        models['outer_model'] = outer_models
        rmses['outer_model'] = outer_rmses
        errors['outer_model'] = outer_errors

        # Initialize/pull the information for the inner feature set
        inner_x = {}
        inner_p_docs = {}
        inner_x['no_block'], _, inner_p_docs['no_block'], inner_pp, inner_norm \
                = getattr(self.puller, inner_feature_set)()
        # Filter the information for the inner feature set information
        if len(blocks) != 1:    # No need to filter if our only block is 'no_block'
            for block in blocks:
                inner_x[block] = self._filter(inner_x['no_block'], self.blocks, block)
                inner_p_docs[block] = self._filter(inner_p_docs['no_block'], self.blocks, block)
        # Add inner-feature-set information to the class attributes
        for block in blocks:
            for dataset, new_p_docs in inner_p_docs[block].iteritems():
                for fp_name, fp_value in new_p_docs.iteritems():
                    if fp_name not in self.p_docs[block][dataset]:
                        self.p_docs[block][dataset][fp_name] = fp_value
        for feature, _pp in inner_pp.iteritems():
            if feature not in self.pp:
                self.pp[feature] = _pp

        # Perform the inner model regression
        models['inner_model'], rmses['inner_model'], errors['inner_model'] = \
                getattr(self, inner_method)(inner_regressor,
                                            x_dict=inner_x,
                                            y_dict=errors['outer_model'])

        # Compile the outputs for the hierarchical model
        for block in blocks:
            # Initialize the sub-structure
            rmses[block] = dict.fromkeys(self.y[block])
            errors[block] = dict.fromkeys(self.y[block])
            # Calculate the rmses and the errors
            for dataset, y in self.y[block].iteritems():
                y_hat = y - errors['inner_model'][block][dataset]
                mse = metrics.mean_squared_error(y, y_hat)
                rmses[block][dataset] = math.sqrt(mse)
                errors[block][dataset] = y - y_hat

        # Create the model
        # TODO:  This function currently assumes that both layers are SK-like.
        # We should probably fix this.
        def _predict(x_outer, x_inner, block):
            '''
            Inputs:
                x_outer An np.array that the outer model may accept directly in order
                        to make its prediction of the final solution
                x_inner An np.array that the inner model may accept directly in order
                        to make its prediction of the outer model's error
            Outputs:
                y_hat   An np.array that represents this hierarchical model's
                        final estimate of the solution
            '''
            # The outer model's estimate of the solution
            y_outer = models['outer_model'][block].predict(x_outer)
            # The inner model's estimate of the outer model's error
            y_inner = models['inner_model'][block].predict(x_inner)
            # The hierarchical model's estimate of the solution
            y_hat = y_outer + y_inner
            return y_hat
        models[block] = __h_model

        # Assign the attributes
        self._predict = _predict
        self.rmses = rmses
        self.errors = errors


    def predict(self, p_docs, block='no_block'):
        ''' This should be pretty obvious '''
        preprocessed_features = self.pp(p_docs) # pylint: disable=not-callable
        predictions = self._predict(preprocessed_features, block)
        return predictions


    def parity_plot(self, xlabel=None, ylabel=None,
                    title=None, lims=None):
        '''
        Create a parity plot of the model that's been fit.

        Input:
            xlabel  A string for the x-axis label
            ylabel  A string for the y-axis label
            title   A string for the title name. If `default`,
                    it turns into a string made up of class attributes.
            lims    A list whose elements decide the bounds within
                    which to create the parity line.
        '''
        # pylint: disable=no-member
        # Establish defaults
        if not title:
            title = 'Predicting %s using a(n) %s model' % (tuple(self.responses), self.model_name)
        if not lims:
            lims = [-4, 6]
        if not xlabel:
            xlabel = 'Simulated %s' % tuple(self.responses)
        if not ylabel:
            ylabel = 'Regressed %s' % tuple(self.responses)

        print('RMSE values:')
        utils.print_dict(self.rmses, indent=1)

        if not self.x:
            raise Exception('Trying to plot without performing a fit first.')

        traces = []
        # Make a plotly trace for each block & dataset
        for block in self.p_docs:
            for dataset in self.p_docs[block]:
                # Don't plot 'train+test', because we're already plotting them separately
                if dataset != 'train+test':
                    # Unpack data from the class attributes
                    x = self.x[block][dataset]
                    y = self.y[block][dataset]
                    p_docs = self.p_docs[block][dataset]
                    errors = self.errors[block][dataset]
                    # Calculate the model's prediction
                    y_hat = y + errors

                    # Make the scatter plots
                    traces.append(go.Scatter(x=y, y=y_hat,
                                             name=str((block, dataset)),
                                             mode='markers',
                                             text='foo'))
        # Make a parity line
        traces.append(go.Scatter(x=lims, y=lims,
                                 name='parity line',
                                 line=dict(color=('block'), dash='dash')))

        # Format and show the plot
        layout = go.Layout(xaxis=dict(title=xlabel),
                           yaxis=dict(title=ylabel),
                           title=title)
        iplot(go.Figure(data=traces, layout=layout))
